using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;

// Extract resource-node and Pal-spawn placements from Palworld's World Partition cells.
//
// The pak is unencrypted (zero encryption GUID, bEncryptedIndex=0), so no AES key is
// needed - only the community usmap for property parsing.
//
// Actor positions are NOT all in world space. Nodes scattered by a designer placement
// volume (BP_BoxPlacementTool*) store RelativeLocation relative to that volume, so
// their raw values are small and cluster near the origin. Taken literally they map to
// (-344, 271) - a real-looking spot on the map with nothing there. This program walks
// each actor's Owner chain and composes the parent transforms to recover world space.
//
//   dotnet run                 full scan
//   dotnet run -- 200          first 200 cells (smoke test)

var limit = args.Length > 0 && int.TryParse(args[0], out var l) ? l : int.MaxValue;

var pakDir = @"C:\Program Files (x86)\Steam\steamapps\common\Palworld\Pal\Content\Paks";
var root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", ".."));
var repo = Path.GetFullPath(Path.Combine(root, "..", ".."));
var outDir = Path.Combine(repo, "data", "raw");
Directory.CreateDirectory(outDir);

// Validated world -> in-game map transform (data/coord_transform.json).
const double SCALE = 458.7383, OFFSET_X = -124238.1, OFFSET_Y = 157818.3;
static (double mx, double my) ToMap(FVector v) =>
    ((v.Y - OFFSET_Y) / SCALE, (v.X - OFFSET_X) / SCALE);

// Owner chains are shallow in practice; the cap only guards against a cycle.
const int MAX_OWNER_DEPTH = 8;

var provider = new DefaultFileProvider(
    pakDir, SearchOption.TopDirectoryOnly, new VersionContainer(EGame.GAME_UE5_1),
    StringComparer.OrdinalIgnoreCase);
provider.Initialize();
provider.Mount();
provider.MappingsContainer = new FileUsmapTypeMappingsProvider(
    Path.Combine(root, "Mappings.usmap"), StringComparer.OrdinalIgnoreCase);

var cells = provider.Files.Keys
    .Where(p => p.Contains("PL_MainWorld5/_Generated_/", StringComparison.OrdinalIgnoreCase)
                && p.EndsWith(".umap", StringComparison.OrdinalIgnoreCase))
    .OrderBy(p => p).Take(limit).ToList();
Console.WriteLine($"scanning {cells.Count:N0} world partition cells\n");

static (FVector loc, FRotator rot, FVector scale)? LocalTransform(UObject actor)
{
    UObject? comp = null;
    if (actor.TryGetValue(out FPackageIndex rootIdx, "RootComponent")) comp = rootIdx.Load();
    var src = comp ?? actor;
    if (!src.TryGetValue(out FVector loc, "RelativeLocation")) return null;
    src.TryGetValue(out FRotator rot, "RelativeRotation");
    if (!src.TryGetValue(out FVector scale, "RelativeScale3D")) scale = new FVector(1, 1, 1);
    return (loc, rot, scale);
}

static string OwnerName(UObject actor)
{
    if (!actor.TryGetValue(out FPackageIndex owner, "Owner")) return "";
    var n = owner.Name ?? "";
    var dot = n.LastIndexOf('.');
    return dot >= 0 ? n[(dot + 1)..] : n;
}

var records = new List<object>();
var classCounts = new Dictionary<string, int>(StringComparer.Ordinal);
int resolvedViaOwner = 0, unresolvedOwner = 0, failedCells = 0, noTransform = 0;
var sw = System.Diagnostics.Stopwatch.StartNew();
var done = 0;

foreach (var cell in cells)
{
    try
    {
        var pkg = provider.LoadPackage(cell[..cell.LastIndexOf('.')]);
        var exports = pkg.GetExports().ToList();

        var byName = new Dictionary<string, UObject>(StringComparer.Ordinal);
        foreach (var e in exports) byName.TryAdd(e.Name, e);

        foreach (var exp in exports)
        {
            var cls = exp.Class?.Name.ToString() ?? exp.ExportType ?? "";
            var isNode = cls.StartsWith("BP_PalMapObjectSpawner", StringComparison.Ordinal);
            var isSpawn = cls.StartsWith("BP_PalSpawner_Sheets", StringComparison.Ordinal);
            if (!isNode && !isSpawn) continue;

            classCounts[cls] = classCounts.GetValueOrDefault(cls, 0) + 1;

            var self = LocalTransform(exp);
            if (self is null) { noTransform++; continue; }

            // Compose up the Owner chain. A node scattered inside a placement volume
            // is stored relative to that volume, so the raw value alone is meaningless.
            var pos = self.Value.loc;
            var hops = 0;
            var current = exp;
            var ownerMissing = false;
            while (hops < MAX_OWNER_DEPTH)
            {
                var ownerName = OwnerName(current);
                if (ownerName.Length == 0) break;
                if (!byName.TryGetValue(ownerName, out var parent))
                {
                    // Owner lives in another cell - cannot resolve, so don't pretend to.
                    ownerMissing = true;
                    break;
                }
                var pTf = LocalTransform(parent);
                if (pTf is null) break;
                var scaled = new FVector(pos.X * pTf.Value.scale.X,
                                         pos.Y * pTf.Value.scale.Y,
                                         pos.Z * pTf.Value.scale.Z);
                var rotated = pTf.Value.rot.RotateVector(scaled);
                pos = new FVector(pTf.Value.loc.X + rotated.X,
                                  pTf.Value.loc.Y + rotated.Y,
                                  pTf.Value.loc.Z + rotated.Z);
                current = parent;
                hops++;
            }

            if (ownerMissing) { unresolvedOwner++; continue; }
            if (hops > 0) resolvedViaOwner++;

            var (mx, my) = ToMap(pos);
            records.Add(new
            {
                kind = isNode ? "node" : "pal_spawn",
                cls,
                cell = Path.GetFileNameWithoutExtension(cell),
                owner_hops = hops,
                world_x = Math.Round(pos.X, 2),
                world_y = Math.Round(pos.Y, 2),
                world_z = Math.Round(pos.Z, 2),
                map_x = Math.Round(mx, 1),
                map_y = Math.Round(my, 1),
            });
        }
    }
    catch { failedCells++; }

    if (++done % 2000 == 0)
        Console.WriteLine($"  {done:N0}/{cells.Count:N0} cells  ({records.Count:N0} placements, {sw.Elapsed.TotalSeconds:F0}s)");
}
sw.Stop();

Console.WriteLine($"\ndone in {sw.Elapsed.TotalMinutes:F1} min");
Console.WriteLine($"  placements extracted : {records.Count:N0}");
Console.WriteLine($"  resolved via Owner   : {resolvedViaOwner:N0}");
Console.WriteLine($"  owner outside cell   : {unresolvedOwner:N0}  (excluded)");
Console.WriteLine($"  no transform         : {noTransform:N0}  (excluded)");
Console.WriteLine($"  cells failed         : {failedCells:N0}");

File.WriteAllText(Path.Combine(outDir, "placements.json"),
    JsonConvert.SerializeObject(records, Formatting.None));
File.WriteAllText(Path.Combine(outDir, "placement_class_counts.json"),
    JsonConvert.SerializeObject(
        classCounts.OrderByDescending(k => k.Value).ToDictionary(k => k.Key, k => k.Value),
        Formatting.Indented));
Console.WriteLine($"\n-> {Path.Combine(outDir, "placements.json")}");
