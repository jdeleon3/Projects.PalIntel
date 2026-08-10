using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion.Textures;
using Newtonsoft.Json;
using SkiaSharp;

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
// NOTE THE `--`. It is dotnet's own argument separator, not part of the mode name:
// `dotnet run -- tables` reaches this program, `dotnet run --tables` does not and used
// to fall through to a 3.6-minute full scan instead. No arguments now prints this list.
//
//   dotnet run -- cells        full scan
//   dotnet run -- 200          first 200 cells (smoke test)
//   dotnet run -- sheets       spawner sheet -> Pal species tables
//   dotnet run -- drops        resource spawner -> the item it actually yields
//   dotnet run -- items        item id -> English name and category
//   dotnet run -- textures     world map basemaps + Pal icons -> PNG
//   dotnet run -- paldrops     what each Pal drops when defeated or captured
//   dotnet run -- ranch        which Pals can be ranched (roster only - see the spike)
//   dotnet run -- probe <s>    list pak paths containing <s> (asset discovery)
//   dotnet run -- dump <path>  print one asset's exports as JSON
//   dotnet run -- tables [s]   list every data table (optionally filtered)
//   dotnet run -- table <name> write one table's rows to data/raw/tables/, list fields
//   dotnet run -- export <s>   write every table matching <s>, same output
//
// The cell scan says WHERE a BP_PalSpawner_Sheets_* actor stands; it says nothing about
// which Pal it spawns. That lives in the sheet blueprint's own default object, which is
// a different asset tree entirely - hence the second mode.

// UTF-8 on stdout. Redirected output otherwise goes out in the console codepage, which
// turns every non-ASCII character in a text table into a byte no JSON parser accepts -
// and the failure looks like a malformed dump rather than an encoding one.
Console.OutputEncoding = System.Text.Encoding.UTF8;

// Modes that read the pak and write a dataset. Anything not on this list is a typo, and
// a typo must not silently start the most expensive mode there is - which is what
// happened when `dotnet run --tables` (no separator) delivered zero arguments and the
// default was a full World Partition scan.
var MODES = new[] { "cells", "sheets", "drops", "items", "textures", "paldrops",
                    "probe", "dump", "tables", "table", "export", "ranch" };

var mode = args.Length > 0 && !int.TryParse(args[0], out _) ? args[0] : "";
var limit = args.Length > 0 && int.TryParse(args[0], out var l) ? l : int.MaxValue;

if (mode.Length > 0 && !MODES.Contains(mode))
{
    Console.WriteLine($"Unknown mode '{mode}'.");
    mode = "";
}

if (mode.Length == 0 && limit == int.MaxValue)
{
    Console.WriteLine("""
        PakExtract - read Palworld's pak. Note the `--`: it separates dotnet's own
        arguments from this program's, so `dotnet run -- tables`, not `--tables`.

          cells        full World Partition scan (~3.6 min) -> placements.json
          <n>          first n cells, as a smoke test
          sheets       spawner sheet -> Pal species tables
          drops        resource spawner -> the item it actually yields
          items        item id -> English name and category
          paldrops     what each Pal drops when defeated or captured
          ranch        which Pals can be ranched (roster only)
          textures     world map basemaps + Pal and item icons -> PNG
          probe <s>    pak paths containing <s>
          dump <path>  one asset's exports as JSON, to stdout
          tables [s]   every data table, optionally filtered
          table <name> one table's rows -> data/raw/tables/, and its field list
          export <s>   every table whose path contains <s>, same output
        """);
    return;
}

if (mode.Length == 0) mode = "cells";      // a bare cell count is still a cell scan

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

if (mode == "probe")
{
    var needle = args.Length > 1 ? args[1] : "Spawner";
    var hits = provider.Files.Keys
        .Where(p => p.Contains(needle, StringComparison.OrdinalIgnoreCase)
                    && !p.Contains("_Generated_", StringComparison.OrdinalIgnoreCase))
        .OrderBy(p => p).ToList();
    Console.WriteLine($"{hits.Count:N0} paths containing '{needle}'");
    foreach (var h in hits.Take(200)) Console.WriteLine("  " + h);
    // Say so when the list is cut. A silent truncation here reads as a complete answer,
    // and counting the printed lines instead of the header is how an asset survey ends
    // up short - it did, while scoping the icon work.
    if (hits.Count > 200)
        Console.WriteLine($"  ... {hits.Count - 200:N0} more (narrow the needle)");
    return;
}

if (mode == "dump")
{
    var path = args[1];
    var pkg = provider.LoadPackage(path);
    Console.WriteLine(JsonConvert.SerializeObject(pkg.GetExports(), Formatting.Indented));
    return;
}

if (mode == "tables")
{
    // Every data table, so a search for "surely there is a table for X" can be answered
    // by looking rather than by guessing names. L10N is collapsed to the English copy:
    // the same table ships once per language and thirty copies is noise.
    var needle = args.Length > 1 ? args[1] : "";
    var all = provider.Files.Keys
        .Where(p => p.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                    && Path.GetFileName(p).StartsWith("DT_", StringComparison.Ordinal)
                    && !p.Contains("_Generated_", StringComparison.OrdinalIgnoreCase))
        .Where(p => !p.Contains("/L10N/", StringComparison.OrdinalIgnoreCase)
                    || p.Contains("/L10N/en/", StringComparison.OrdinalIgnoreCase))
        .Where(p => needle.Length == 0
                    || p.Contains(needle, StringComparison.OrdinalIgnoreCase))
        .OrderBy(p => p, StringComparer.Ordinal)
        .ToList();

    foreach (var p in all) Console.WriteLine("  " + p[..p.LastIndexOf('.')]);
    Console.WriteLine($"\n{all.Count:N0} data tables"
                      + (needle.Length > 0 ? $" matching '{needle}'" : ""));
    return;
}

if (mode == "table" || mode == "export")
{
    // One table's rows, written straight to a UTF-8 file rather than to stdout. That is
    // the whole point: redirecting `dump` sends the text through the console codepage,
    // where a curly quote in a Paldeck description (U+201C, and several have one) turns
    // into a byte that terminates the JSON string early. The dump then looks corrupt in
    // a way that reads like a serializer bug rather than an encoding one - it cost a
    // wrong diagnosis here before the bytes were actually looked at.
    var wanted = args.Length > 1 ? args[1] : "";
    if (wanted.Length == 0)
    {
        Console.WriteLine($"Usage: dotnet run -- {mode} <{(mode == "table" ? "name" : "substring")}>");
        return;
    }

    // `table` takes one exact name; `export` takes a substring and writes every match.
    // Both skip the non-English L10N copies - the same table ships once per language and
    // thirty identical files is not a browsable directory.
    var candidates = provider.Files.Keys
        .Where(p => p.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                    && Path.GetFileName(p).StartsWith("DT_", StringComparison.Ordinal)
                    && !p.Contains("_Generated_", StringComparison.OrdinalIgnoreCase)
                    && (!p.Contains("/L10N/", StringComparison.OrdinalIgnoreCase)
                        || p.Contains("/L10N/en/", StringComparison.OrdinalIgnoreCase)))
        .Where(p => mode == "table"
                    ? Path.GetFileNameWithoutExtension(p).Equals(wanted, StringComparison.OrdinalIgnoreCase)
                    : p.Contains(wanted, StringComparison.OrdinalIgnoreCase))
        .OrderBy(p => p, StringComparer.Ordinal)
        .ToList();

    if (candidates.Count == 0)
    {
        Console.WriteLine($"Nothing matching '{wanted}'. Try: dotnet run -- tables {wanted}");
        return;
    }

    var tableDir = Path.Combine(outDir, "tables");
    Directory.CreateDirectory(tableDir);
    int written = 0, empty = 0;

    // A table name is not unique across the pak: DT_ItemNameText_Common exists both at
    // Pal/DataTable/Text (whose LocalizedString is Japanese) and at L10N/en (English).
    // Writing both to one filename let the base table silently overwrite the English
    // one, so a browser of data/raw/tables would conclude item names are Japanese.
    // Localised copies are prefixed with their locale instead.
    static string FileNameFor(string path)
    {
        var name = Path.GetFileNameWithoutExtension(path);
        var i = path.IndexOf("/L10N/", StringComparison.OrdinalIgnoreCase);
        if (i < 0) return name;
        var locale = path[(i + 6)..].Split('/')[0];
        return $"{locale}_{name}";
    }

    var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

    foreach (var match in candidates)
    {
        var name = FileNameFor(match);
        if (!seen.Add(name))
        {
            // Should be impossible now, and says so rather than overwriting if it is not.
            Console.WriteLine($"  {name,-46} SKIPPED  name collision with an earlier table");
            continue;
        }
        Newtonsoft.Json.Linq.JObject rows;
        try
        {
            var pkg = provider.LoadPackage(match[..match.LastIndexOf('.')]);
            var json = Newtonsoft.Json.Linq.JObject.Parse(
                JsonConvert.SerializeObject(pkg.GetExports().First()));
            rows = json["Rows"] as Newtonsoft.Json.Linq.JObject
                   ?? new Newtonsoft.Json.Linq.JObject();
        }
        catch (Exception e)
        {
            // Named, not swallowed: a table that will not load is a gap in the export,
            // and a silent one would be indistinguishable from a table that is empty.
            Console.WriteLine($"  {name,-46} FAILED  {e.GetType().Name}");
            continue;
        }

        if (rows.Count == 0) { empty++; }

        var dest = Path.Combine(tableDir, $"{name}.json");
        File.WriteAllText(dest, rows.ToString(Formatting.Indented),
                          new System.Text.UTF8Encoding(false));
        written++;

        // Fields are unioned over a sample rather than the first row: several tables
        // leave optional columns off rows that do not use them.
        var fields = rows.Properties().Take(50)
            .SelectMany(r => (r.Value as Newtonsoft.Json.Linq.JObject)?.Properties()
                                 .Select(f => f.Name) ?? Enumerable.Empty<string>())
            .Distinct().OrderBy(f => f, StringComparer.Ordinal).ToList();

        if (mode == "table")
        {
            Console.WriteLine($"{match}\n  {rows.Count:N0} rows, {fields.Count} fields");
            foreach (var f in fields) Console.WriteLine("    " + f);
        }
        else
        {
            Console.WriteLine($"  {name,-46} {rows.Count,7:N0} rows  {fields.Count,3} fields");
        }
    }

    Console.WriteLine($"\n{written:N0} table{(written == 1 ? "" : "s")} -> {tableDir}"
                      + (empty > 0 ? $"  ({empty} with no rows)" : ""));
    return;
}

if (mode == "ranch")
{
    // The ranch ROSTER, and only the roster. Which Pals can be assigned to a ranch is in
    // the pak - one BP_Action_SpawnItem_<CharacterID> asset each - but *what each one
    // produces* is not: it lives in blueprint bytecode, and all 284 data tables were
    // enumerated without finding it (Docs/04-roadmap.md, ranch spike). The items are
    // sourced from the community wiki instead, and this list is what validates them:
    // a wiki row naming a Pal that is not here, or a Pal here that the wiki omits, is a
    // discrepancy the ingest has to surface rather than absorb.
    const string prefix = "BP_Action_SpawnItem_";
    var roster = provider.Files.Keys
        .Where(p => p.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase))
        .Select(p => Path.GetFileNameWithoutExtension(p))
        .Where(n => n.StartsWith(prefix, StringComparison.Ordinal)
                    && n.Length > prefix.Length)
        .Select(n => n[prefix.Length..])
        .Distinct(StringComparer.Ordinal)
        .OrderBy(n => n, StringComparer.Ordinal)
        .ToList();

    var dest = Path.Combine(outDir, "ranch_roster.json");
    File.WriteAllText(dest, JsonConvert.SerializeObject(new
    {
        source = "BP_Action_SpawnItem_<CharacterID> assets in Pal-Windows.pak",
        note = "Roster only. The produced item is not in any extractable table.",
        character_ids = roster,
    }, Formatting.Indented));
    Console.WriteLine($"{roster.Count:N0} ranchable Pals -> {dest}");
    foreach (var r in roster) Console.WriteLine("  " + r);
    return;
}

if (mode == "paldrops")
{
    // What a Pal drops when defeated or captured. Ten fixed item slots per row, each
    // with its own rate and count range, keyed by CharacterID - so this is emitted
    // verbatim and the inversion (item -> the Pals that drop it) happens in the ingest,
    // where the lexicon lives.
    var pkg = provider.LoadPackage(
        "Pal/Content/Pal/DataTable/Character/DT_PalDropItem_Common");
    var rows = Newtonsoft.Json.Linq.JObject.Parse(
        JsonConvert.SerializeObject(pkg.GetExports().First()))["Rows"]
        as Newtonsoft.Json.Linq.JObject ?? new Newtonsoft.Json.Linq.JObject();

    var out_ = new List<object>();
    foreach (var row in rows)
    {
        var v = row.Value!;
        var drops = new List<object>();
        for (var n = 1; n <= 10; n++)
        {
            var item = v[$"ItemId{n}"]?.ToString();
            if (string.IsNullOrEmpty(item) || item == "None") continue;
            drops.Add(new
            {
                item,
                // Carried rather than filtered here: a rate of 0 is a real row in the
                // table and the ingest has to decide what it means, not the extractor.
                rate = v[$"Rate{n}"]?.ToObject<double>() ?? 0,
                min = v[$"min{n}"]?.ToObject<int>() ?? 0,
                max = v[$"Max{n}"]?.ToObject<int>() ?? 0,
            });
        }
        if (drops.Count == 0) continue;
        out_.Add(new
        {
            row_key = row.Key,
            character_id = v["CharacterID"]?.ToString(),
            level = v["Level"]?.ToObject<int>() ?? 0,
            drops,
        });
    }

    var dest = Path.Combine(outDir, "pal_drops.json");
    File.WriteAllText(dest, JsonConvert.SerializeObject(out_, Formatting.None));
    Console.WriteLine($"{out_.Count:N0} drop rows (of {rows.Count:N0}) -> {dest}");
    return;
}

if (mode == "textures")
{
    // Card artwork: the two world-map basemaps and one icon per Pal.
    //
    // The map bounds are NOT fitted here. DT_WorldMapUIData carries the world-space
    // rectangle each basemap covers, so the world -> pixel mapping is the game's own
    // rather than another regression like data/coord_transform.json. It also says there
    // are TWO maps: placements in the World Tree region sit outside MainMap's rectangle
    // entirely, and drawing them on the main island would put markers in open sea.
    var texDir = Path.Combine(outDir, "textures");
    Directory.CreateDirectory(Path.Combine(texDir, "map"));
    Directory.CreateDirectory(Path.Combine(texDir, "icon"));

    bool WritePng(string assetPath, string dest)
    {
        try
        {
            var pkg = provider.LoadPackage(assetPath);
            var tex = pkg.GetExports().OfType<UTexture2D>().FirstOrDefault();
            if (tex is null) return false;
            var decoded = tex.Decode(ETexturePlatform.DesktopMobile);
            if (decoded is null) return false;
            using var bitmap = decoded.ToSkBitmap();
            if (bitmap is null) return false;
            using var image = SKImage.FromBitmap(bitmap);
            using var data = image.Encode(SKEncodedImageFormat.Png, 100);
            using var file = File.Create(dest);
            data.SaveTo(file);
            return true;
        }
        catch { return false; }
    }

    // "/Game/Pal/Texture/UI/Map/T_WorldMap.T_WorldMap" -> a mounted pak path.
    static string FromAssetPath(string p)
    {
        p = p.Replace("/Game/", "Pal/Content/", StringComparison.Ordinal);
        var dot = p.LastIndexOf('.');
        return dot >= 0 ? p[..dot] : p;
    }

    var uiData = provider.LoadPackage(
        "Pal/Content/Pal/DataTable/WorldMapUIData/DT_WorldMapUIData");
    var uiRows = Newtonsoft.Json.Linq.JObject.Parse(
        JsonConvert.SerializeObject(uiData.GetExports().First()))["Rows"]
        as Newtonsoft.Json.Linq.JObject ?? new Newtonsoft.Json.Linq.JObject();

    var regions = new List<object>();
    foreach (var row in uiRows)
    {
        var v = row.Value!;
        var textures = v["textureDataMap"] ?? new Newtonsoft.Json.Linq.JArray();
        // One basemap per region is what 1.0.2 ships (mapBlockNum is 1x1 for both). A
        // tiled region would need the grid composited, so fail loudly rather than
        // silently exporting the first tile as if it were the whole map.
        var blockX = v["mapBlockNum"]?["X"]?.ToObject<double>() ?? 1;
        var blockY = v["mapBlockNum"]?["Y"]?.ToObject<double>() ?? 1;
        if (blockX != 1 || blockY != 1)
        {
            Console.WriteLine($"  {row.Key}: SKIPPED - {blockX}x{blockY} tile grid, " +
                              $"this mode only handles single-texture regions");
            continue;
        }

        var asset = textures.FirstOrDefault()?["Value"]?["Texture"]?["AssetPathName"]
                    ?.ToString();
        if (string.IsNullOrEmpty(asset) || asset == "None") continue;

        var file = $"{row.Key.ToLowerInvariant()}.png";
        var ok = WritePng(FromAssetPath(asset), Path.Combine(texDir, "map", file));
        var mn = v["landScapeRealPositionMin"]!;
        var mx = v["landScapeRealPositionMax"]!;
        regions.Add(new
        {
            region = row.Key,
            file = ok ? $"map/{file}" : null,
            priority = v["WorldMapPriority"]?.ToObject<int>() ?? 0,
            world_min_x = mn["X"]!.ToObject<double>(),
            world_min_y = mn["Y"]!.ToObject<double>(),
            world_max_x = mx["X"]!.ToObject<double>(),
            world_max_y = mx["Y"]!.ToObject<double>(),
        });
        Console.WriteLine($"  {row.Key,-10} {(ok ? "ok" : "FAILED")}  {asset}");
    }

    var icons = provider.Files.Keys
        .Where(p => p.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                    && p.Contains("Texture/PalIcon/Normal/", StringComparison.OrdinalIgnoreCase))
        .OrderBy(p => p, StringComparer.Ordinal).ToList();
    Console.WriteLine($"\ndecoding {icons.Count:N0} Pal icons");

    var written = new List<string>();
    var failedIcons = new List<string>();
    foreach (var p in icons)
    {
        // T_Anubis_icon_normal -> Anubis, the CharacterID the lexicon already keys on.
        var stem = Path.GetFileNameWithoutExtension(p);
        var id = stem.StartsWith("T_", StringComparison.Ordinal) ? stem[2..] : stem;
        if (id.EndsWith("_icon_normal", StringComparison.Ordinal))
            id = id[..^"_icon_normal".Length];

        if (WritePng(p[..p.LastIndexOf('.')], Path.Combine(texDir, "icon", $"{id}.png")))
            written.Add(id);
        else failedIcons.Add(id);
    }

    // Item icons, for the resource cards. Category is part of the filename and varies
    // (Material, Food, Ammo...), so the stem is kept whole rather than parsed here -
    // item ids contain underscores of their own (Pal_crystal_S, Wood_Ancient) and
    // splitting on them upstream would be guessing at where the id starts.
    Directory.CreateDirectory(Path.Combine(texDir, "item"));
    var itemIcons = provider.Files.Keys
        .Where(p => p.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                    && Path.GetFileName(p).StartsWith("T_itemicon_", StringComparison.Ordinal))
        .OrderBy(p => p, StringComparer.Ordinal).ToList();
    Console.WriteLine($"\ndecoding {itemIcons.Count:N0} item icons");

    var itemsWritten = new List<string>();
    foreach (var p in itemIcons)
    {
        var stem = Path.GetFileNameWithoutExtension(p)["T_itemicon_".Length..];
        if (WritePng(p[..p.LastIndexOf('.')], Path.Combine(texDir, "item", $"{stem}.png")))
            itemsWritten.Add(stem);
    }
    Console.WriteLine($"  item icons written : {itemsWritten.Count:N0}");

    var manifest = Path.Combine(texDir, "manifest.json");
    File.WriteAllText(manifest, JsonConvert.SerializeObject(new
    {
        game_version = "1.0.2",
        source = "Pal-Windows.pak: DT_WorldMapUIData, Texture/UI/Map, Texture/PalIcon/Normal",
        note = "World -> pixel comes from landScapeRealPosition, the game's own map "
             + "bounds, not from a fit. A coordinate outside every region has no map.",
        map_regions = regions,
        icons = written,
        item_icons = itemsWritten,
    }, Formatting.Indented));

    Console.WriteLine($"\n  icons written : {written.Count:N0}");
    Console.WriteLine($"  icons failed  : {failedIcons.Count:N0}"
                      + (failedIcons.Count > 0 ? "  " + string.Join(", ", failedIcons.Take(10)) : ""));
    Console.WriteLine($"\n-> {manifest}");
    return;
}

if (mode == "items")
{
    // Name and category for every item, joined here because both tables are open and
    // neither is useful alone: the data table says Stone is a MaterialStone, the English
    // text table says the player calls it "Stone", and the ingest needs both to decide
    // what belongs in the resource enum and what to print on a card.
    var data = provider.LoadPackage("Pal/Content/Pal/DataTable/Item/DT_ItemDataTable_Common");
    var text = provider.LoadPackage("Pal/Content/L10N/en/Pal/DataTable/Text/DT_ItemNameText_Common");

    var rows = Newtonsoft.Json.Linq.JObject.Parse(
        JsonConvert.SerializeObject(data.GetExports().First()))["Rows"]
        as Newtonsoft.Json.Linq.JObject ?? new Newtonsoft.Json.Linq.JObject();
    var names = Newtonsoft.Json.Linq.JObject.Parse(
        JsonConvert.SerializeObject(text.GetExports().First()))["Rows"]
        as Newtonsoft.Json.Linq.JObject ?? new Newtonsoft.Json.Linq.JObject();

    var items = new Dictionary<string, object>(StringComparer.Ordinal);
    foreach (var row in rows)
    {
        var localised = names[$"ITEM_NAME_{row.Key}"]?["TextData"]?["LocalizedString"]
                        ?.ToString();
        // "en Text" is the game's own untranslated-row placeholder, and "None" is an
        // empty row. Neither is a name, and letting either through would put a matchable
        // entity called "en Text" in the lexicon.
        if (localised is null or "None" or "en Text" or "") localised = null;
        items[row.Key] = new
        {
            name = localised,
            type_a = row.Value?["TypeA"]?.ToString()?.Split("::").Last(),
            type_b = row.Value?["TypeB"]?.ToString()?.Split("::").Last(),
            rank = row.Value?["Rank"]?.ToObject<int?>(),
        };
    }

    var itemsOut = Path.Combine(outDir, "items.json");
    File.WriteAllText(itemsOut, JsonConvert.SerializeObject(items, Formatting.None));
    Console.WriteLine($"{items.Count:N0} items -> {itemsOut}");
    return;
}

if (mode == "drops")
{
    // Which resource a BP_PalMapObjectSpawner_* yields is four hops away from the actor,
    // and every hop is in the data:
    //
    //   spawner CDO  ->  MapObjectId ("DamagableRock0002")
    //   master table ->  BlueprintClassSoft (the map object that gets mined)
    //   map object   ->  PalMapObjectDropItemParameterComponent
    //   component    ->  DropItems[].StaticItemId ("CopperOre")
    //
    // MaterialSubType on the master row is NOT the answer - it is the tool category, and
    // it lumps coal, sulfur and quartz together as "Copper". It is carried anyway because
    // it distinguishes what a node is mined WITH, which the drop id does not.
    var masters = new Dictionary<string, Newtonsoft.Json.Linq.JObject>(StringComparer.Ordinal);
    foreach (var table in new[] { "DT_MapObjectMasterDataTable_Common",
                                  "DT_MapObjectMasterDataTable_EnemyCamp" })
    {
        var pkg = provider.LoadPackage($"Pal/Content/Pal/DataTable/MapObject/{table}");
        var json = Newtonsoft.Json.Linq.JObject.Parse(
            JsonConvert.SerializeObject(pkg.GetExports().First()));
        foreach (var row in (Newtonsoft.Json.Linq.JObject?)json["Rows"]
                            ?? new Newtonsoft.Json.Linq.JObject())
            masters[row.Key] = (Newtonsoft.Json.Linq.JObject)row.Value!;
    }
    Console.WriteLine($"master rows: {masters.Count:N0}");

    Newtonsoft.Json.Linq.JToken? DropsOf(string assetPath)
    {
        // "/Game/Pal/Blueprint/.../BP_X.BP_X_C" -> "Pal/Content/Pal/Blueprint/.../BP_X"
        if (string.IsNullOrEmpty(assetPath) || assetPath == "None") return null;
        var path = assetPath.Replace("/Game/", "Pal/Content/", StringComparison.Ordinal);
        var dot = path.LastIndexOf('.');
        if (dot >= 0) path = path[..dot];

        // Two component classes, because a node you mine and an item you walk over are
        // different objects in the game's model. They carry the same DropItems shape, so
        // the distinction is only in where to look: a rock uses DropItemParameter, a log
        // or a berry uses PickupItemParameter. Matching only the first silently dropped
        // the two largest classes in the world (4,654 logs and 4,286 small stones).
        var pkg = provider.LoadPackage(path);
        foreach (var exp in pkg.GetExports())
        {
            var component = exp.Class?.Name.ToString();
            if (component != "PalMapObjectDropItemParameterComponent"
                && component != "PalMapObjectPickupItemParameterComponent")
                continue;
            var json = Newtonsoft.Json.Linq.JObject.Parse(JsonConvert.SerializeObject(exp));
            var drops = json["Properties"]?["DropItems"];
            if (drops is not null && drops.HasValues) return drops;
        }
        return null;
    }

    var spawnerPaths = provider.Files.Keys
        .Where(p => p.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                    && Path.GetFileName(p).StartsWith("BP_PalMapObjectSpawner",
                                                      StringComparison.Ordinal))
        .OrderBy(p => p).ToList();
    Console.WriteLine($"reading {spawnerPaths.Count:N0} map-object spawners\n");

    var out_ = new List<object>();
    int noId = 0, noMaster = 0, noDrops = 0, failed = 0;
    foreach (var p in spawnerPaths)
    {
        var cls = Path.GetFileNameWithoutExtension(p) + "_C";
        try
        {
            var pkg = provider.LoadPackage(p[..p.LastIndexOf('.')]);
            var cdo = pkg.GetExports().FirstOrDefault(
                e => e.Name.StartsWith("Default__", StringComparison.Ordinal));
            if (cdo is null) { noId++; continue; }

            var json = Newtonsoft.Json.Linq.JObject.Parse(JsonConvert.SerializeObject(cdo));
            var objectId = json["Properties"]?["MapObjectId"]?["Key"]?.ToString();
            if (string.IsNullOrEmpty(objectId) || objectId == "None") { noId++; continue; }
            if (!masters.TryGetValue(objectId, out var master)) { noMaster++; continue; }

            var drops = DropsOf(master["BlueprintClassSoft"]?["AssetPathName"]?.ToString() ?? "");
            if (drops is null) noDrops++;

            out_.Add(new
            {
                cls,
                map_object_id = objectId,
                material_type = master["MaterialType"]?.ToString(),
                material_sub_type = master["MaterialSubType"]?.ToString(),
                hp = master["Hp"]?.ToObject<int>(),
                drops,
            });
        }
        catch (Exception e) { failed++; Console.WriteLine($"  FAILED {cls}: {e.Message}"); }
    }

    Console.WriteLine($"\n  spawners resolved   : {out_.Count:N0}");
    Console.WriteLine($"  no MapObjectId      : {noId:N0}");
    Console.WriteLine($"  id not in master    : {noMaster:N0}");
    Console.WriteLine($"  resolved, no drops  : {noDrops:N0}");
    Console.WriteLine($"  failed to load      : {failed:N0}");

    var dropsOut = Path.Combine(outDir, "node_drops.json");
    File.WriteAllText(dropsOut, JsonConvert.SerializeObject(out_, Formatting.None));
    Console.WriteLine($"\n-> {dropsOut}");
    return;
}

if (mode == "sheets")
{
    // The class default object carries SpawnGroupList. Variant sheets frequently define
    // nothing of their own and inherit the whole table from a parent blueprint, so an
    // absent list is not an empty spawner - it means "ask the parent". Following the
    // Template chain rather than treating absence as empty is the difference between
    // 411 populated sheets and a silent hole wherever a designer used inheritance.
    const int MAX_TEMPLATE_DEPTH = 8;

    Newtonsoft.Json.Linq.JToken? SpawnGroups(string pkgPath, out string resolvedFrom)
    {
        resolvedFrom = pkgPath;
        for (var depth = 0; depth < MAX_TEMPLATE_DEPTH; depth++)
        {
            var pkg = provider.LoadPackage(resolvedFrom);
            var cdo = pkg.GetExports().FirstOrDefault(
                e => e.Name.StartsWith("Default__", StringComparison.Ordinal));
            if (cdo is null) return null;

            var json = Newtonsoft.Json.Linq.JObject.Parse(JsonConvert.SerializeObject(cdo));
            var groups = json["Properties"]?["SpawnGroupList"];
            if (groups is not null && groups.HasValues) return groups;

            // Template points at the parent CDO as "<package>.<exportIndex>".
            var template = json["Template"]?["ObjectPath"]?.ToString();
            if (string.IsNullOrEmpty(template)) return null;
            var dot = template.LastIndexOf('.');
            var parent = dot >= 0 ? template[..dot] : template;
            if (parent == resolvedFrom) return null;
            resolvedFrom = parent;
        }
        return null;
    }

    var sheetPaths = provider.Files.Keys
        .Where(p => p.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                    && Path.GetFileName(p).StartsWith("BP_PalSpawner_Sheets",
                                                      StringComparison.Ordinal))
        .OrderBy(p => p).ToList();
    Console.WriteLine($"reading {sheetPaths.Count:N0} spawner sheet blueprints\n");

    var sheets = new List<object>();
    int inherited = 0, empty = 0, failed = 0;
    foreach (var p in sheetPaths)
    {
        var pkgPath = p[..p.LastIndexOf('.')];
        var cls = Path.GetFileNameWithoutExtension(p) + "_C";
        try
        {
            var groups = SpawnGroups(pkgPath, out var from);
            if (groups is null) { empty++; continue; }
            if (from != pkgPath) inherited++;
            sheets.Add(new
            {
                cls,
                package = pkgPath,
                inherited_from = from == pkgPath ? null : from,
                spawn_group_list = groups,
            });
        }
        catch (Exception e) { failed++; Console.WriteLine($"  FAILED {cls}: {e.Message}"); }
    }

    Console.WriteLine($"\n  sheets with a table : {sheets.Count:N0}");
    Console.WriteLine($"  inherited from parent: {inherited:N0}");
    Console.WriteLine($"  no table found       : {empty:N0}");
    Console.WriteLine($"  failed to load       : {failed:N0}");

    var sheetsOut = Path.Combine(outDir, "spawner_sheets.json");
    File.WriteAllText(sheetsOut, JsonConvert.SerializeObject(sheets, Formatting.None));
    Console.WriteLine($"\n-> {sheetsOut}");
    return;
}

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
