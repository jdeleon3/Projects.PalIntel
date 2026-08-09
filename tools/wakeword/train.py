"""Runner for openWakeWord training, with the compatibility shims it needs.

`python -m openwakeword.train` does not currently run against a modern dependency set.
Eight breakages, all version drift, Windows portability, or third-party bugs rather
than anything wrong with the config:

  1. `openwakeword.data` imports `acoustics`, whose package `__init__` eagerly imports
     `acoustics.directivity`, which imports `scipy.special.sph_harm` - removed in SciPy
     1.17 and replaced by `sph_harm_y`. openWakeWord uses `acoustics` for exactly one
     call (`acoustics.generator.noise`, for coloured augmentation noise) and never
     touches directivity, so aliasing the name is enough to get past the import.

  2. `openwakeword.train` does `from generate_samples import generate_samples`, a module
     that piper-sample-generator moved into a package in commit 1a8c49b. The checkout
     must be pinned to 1a8c49b~1; this script fails loudly if it is not, because the
     alternative is a confusing ImportError several layers down.

  3. Even at that commit the signatures disagree: openWakeWord calls `generate_samples`
     without `model`, expecting a version where it defaulted, while the pinned one takes
     it as a required positional. The function is wrapped here to supply the voice from
     the config, which is better than pinning further back and losing `**kwargs` (which
     is what silently absorbs `auto_reduce_batch_size`).

  4. `torchaudio.load` in 2.11 delegates to TorchCodec, which is not installed and would
     pull in an FFmpeg dependency on Windows for no benefit. `soundfile` (already a
     dependency) reads these files directly, so `load` is redirected rather than the
     dependency added.

  5. Piper's libritts_r voice synthesises at 22,050Hz and openWakeWord's augmentation
     asserts 16,000Hz, so every generated clip fails the check. The shim resamples on
     load, which fixes 64,000 files without rewriting any of them - and note the failure
     mode it prevents: the first run wrote a 176MB feature file from 22kHz audio before
     erroring, so a partial run leaves behind features that look valid and are not.

  6. `data.trim_mmap` replaces a file that is still memory-mapped - legal on Linux,
     PermissionError on Windows, and it strikes at the very last step after all the
     augmentation work is done. The open handle belongs to the *caller*
     (`compute_features_from_generator` opens the output, fills it, then trims while
     still holding it), so no fix inside `trim_mmap` can reach it. The trim is deferred
     instead: the inner call records the path, and the real work runs once the caller
     has returned. Also fixes `mmap_path.strip(".npy")`, which strips *characters*
     rather than a suffix and produced "positive_features_trai2.npy" by eating the "n"
     from "train".

  7. `speechbrain.utils.importutils.LazyModule.__getattr__` raises ImportError for any
     attribute, dunders included. `hasattr` only swallows AttributeError, so when
     `inspect.getmodule` walks sys.modules doing `hasattr(module, "__file__")` - which
     torchinfo does while summarising the model - an unrelated optional dependency
     (k2-fsa) kills the run at the final step.

  8. openWakeWord defines `IterDataset` in its `__main__`, and Windows DataLoader
     workers spawn and pickle the dataset class by qualified name - which a
     runpy-executed `__main__` cannot provide. Loading is forced single-process; the
     features are already memory-mapped, so the loader slices arrays rather than
     decoding audio and the throughput cost is irrelevant.

Usage (from the repo root):

    python tools/wakeword/train.py --generate_clips
    python tools/wakeword/train.py --augment_clips
    python tools/wakeword/train.py --train_model
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

CONFIG = Path(__file__).with_name("hey_pal.yaml")


def _shim_scipy() -> None:
    """Restore the name `acoustics` imports. Never called - only imported."""
    import scipy.special as sp
    if not hasattr(sp, "sph_harm") and hasattr(sp, "sph_harm_y"):
        sp.sph_harm = sp.sph_harm_y


TARGET_SR = 16_000


def _shim_torchaudio() -> None:
    """Back torchaudio.load with soundfile, resampling to 16kHz.

    Returns the (channels, samples) float32 tensor and sample rate that callers expect;
    soundfile hands back (samples, channels), so the transpose matters - getting it
    wrong yields a 1-sample, N-channel clip and augmentation fails much later with an
    error that says nothing about audio layout.
    """
    import numpy as np
    import soundfile as sf
    import soxr
    import torch
    import torchaudio

    def load(path, *args, **kwargs):
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        if sr != TARGET_SR:
            data = soxr.resample(data, sr, TARGET_SR)
            sr = TARGET_SR
        return torch.from_numpy(np.ascontiguousarray(data.T)), sr

    class _Info:
        """The three attributes openwakeword.data reads off torchaudio.info.

        Reported post-resample, so `sample_rate` matches what `load` actually returns -
        otherwise duration maths done from `info` disagrees with the array from `load`
        and clips get silently mis-sliced.
        """

        def __init__(self, path):
            i = sf.info(str(path))
            self.sample_rate = TARGET_SR
            self.num_channels = i.channels
            self.num_frames = int(i.frames * TARGET_SR / i.samplerate)

    torchaudio.load = load
    torchaudio.info = _Info


def _shim_trim_mmap() -> None:
    """Trim trailing all-zero rows, after every handle on the file is closed.

    `compute_features_from_generator` opens the output as a memmap, fills it, and calls
    `trim_mmap` *while still holding it*. On Linux the replace succeeds anyway; on
    Windows it cannot, and no fix inside `trim_mmap` can reach the caller's handle.

    So the trim is deferred: the in-function call only records the path, and the real
    work runs once the caller has returned and its mmap has gone out of scope.
    """
    import gc
    import os as _os

    import numpy as np
    from numpy.lib.format import open_memmap

    import openwakeword.data as data
    import openwakeword.utils as utils

    pending: list[str] = []

    def real_trim(mmap_path: str) -> None:
        src = np.load(mmap_path, mmap_mode="r")
        i = -1
        while np.all(src[i, :, :] == 0):
            i -= 1
        n_new = src.shape[0] + i + 1

        tmp = f"{mmap_path}.trim"
        dst = open_memmap(tmp, mode="w+", dtype=np.float32,
                          shape=(n_new, src.shape[1], src.shape[2]))
        for j in range(0, n_new, 1024):
            k = min(j + 1024, n_new)
            dst[j:k] = src[j:k]
        dst.flush()

        # numpy keeps the mapping alive through the array's base, so dropping the names
        # is not enough on its own - the collect is load-bearing, not defensive.
        src._mmap.close()
        del dst, src
        gc.collect()
        _os.replace(tmp, mmap_path)

    data.trim_mmap = pending.append

    _orig = utils.compute_features_from_generator

    def compute_features_from_generator(*args, **kwargs):
        result = _orig(*args, **kwargs)
        gc.collect()                     # the caller's `fp` is out of scope now
        while pending:
            real_trim(pending.pop())
        return result

    utils.compute_features_from_generator = compute_features_from_generator


def _shim_speechbrain() -> None:
    """Make speechbrain's lazy modules survive attribute probing.

    `LazyModule.__getattr__` raises ImportError for *any* attribute, including dunders.
    `hasattr` only swallows AttributeError, so when `inspect.getmodule` walks sys.modules
    doing `hasattr(module, "__file__")` - which torchinfo does while summarising the
    model - an unrelated optional dependency (k2-fsa) takes the whole training run down
    at the last step.

    Dunder lookups now raise AttributeError, which is what the protocol expects and what
    makes hasattr return False. Real attribute access is untouched.
    """
    try:
        from speechbrain.utils.importutils import LazyModule
    except ImportError:
        return

    original = LazyModule.__getattr__

    def __getattr__(self, attr):
        if attr.startswith("__") and attr.endswith("__"):
            try:
                return original(self, attr)
            except ImportError as e:
                raise AttributeError(attr) from e
        return original(self, attr)

    LazyModule.__getattr__ = __getattr__


def _shim_dataloader() -> None:
    """Force single-process data loading.

    openWakeWord defines `IterDataset` inside its `__main__`, and Windows DataLoader
    workers use spawn, which pickles the dataset class by qualified name. A class living
    in a `runpy`-executed `__main__` cannot be re-imported by the child, so every worker
    dies with `Can't pickle <class '__main__.IterDataset'>`.

    Single-process loading costs throughput and nothing else here: the features are
    already computed and memory-mapped, so the loader is slicing arrays rather than
    decoding audio.
    """
    import torch.utils.data as tud

    _orig = tud.DataLoader.__init__

    def __init__(self, *args, **kwargs):
        kwargs["num_workers"] = 0
        kwargs.pop("prefetch_factor", None)      # invalid when num_workers is 0
        kwargs.pop("persistent_workers", None)
        return _orig(self, *args, **kwargs)

    tud.DataLoader.__init__ = __init__


def _piper_path() -> str:
    import yaml
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    path = Path(cfg["piper_sample_generator_path"])
    if not (path / "generate_samples.py").exists():
        raise SystemExit(
            f"{path}/generate_samples.py is missing.\n"
            f"piper-sample-generator moved it into a package; check out the last\n"
            f"compatible commit:\n\n"
            f"    cd {path} && git checkout 1a8c49b~1\n")
    return str(path)


def _bind_voice(piper_dir: str) -> None:
    """Pre-import generate_samples with the voice bound, so train.py picks it up.

    train.py does `from generate_samples import generate_samples` *after* extending
    sys.path, so replacing the attribute on the already-imported module is enough - no
    patching of openWakeWord itself, and the fix disappears if upstream ever aligns.
    """
    import functools
    import yaml

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    voice = cfg.get("piper_voice") or str(
        Path(piper_dir) / "models" / "en_US-libritts_r-medium.pt")
    if not Path(voice).exists():
        raise SystemExit(f"piper voice model missing: {voice}")

    sys.path.insert(0, piper_dir)
    import generate_samples as gs
    gs.generate_samples = functools.partial(gs.generate_samples, model=voice)


def main() -> None:
    _shim_scipy()
    _shim_torchaudio()
    _shim_trim_mmap()
    _shim_speechbrain()
    _shim_dataloader()
    # train.py resolves the generator relative to cwd, and imports it by bare name.
    piper = _piper_path()
    _bind_voice(piper)
    os.chdir(piper)
    sys.argv = ["openwakeword.train", "--training_config", str(CONFIG), *sys.argv[1:]]
    runpy.run_module("openwakeword.train", run_name="__main__")


if __name__ == "__main__":
    main()
