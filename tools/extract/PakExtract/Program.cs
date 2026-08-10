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
//   dotnet run -- sheets       spawner sheet -> Pal species tables
//   dotnet run -- drops        resource spawner -> the item it actually yields
//   dotnet run -- items        item id -> English name and category
//   dotnet run -- probe <s>    list pak paths containing <s> (asset discovery)
//   dotnet run -- dump <path>  print one asset's exports as JSON
//
// The cell scan says WHERE a BP_PalSpawner_Sheets_* actor stands; it says nothing about
// which Pal it spawns. That lives in the sheet blueprint's own default object, which is
// a different asset tree entirely - hence the second mode.

var mode = args.Length > 0 && !int.TryParse(args[0], out _) ? args[0] : "cells";
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

if (mode == "probe")
{
    var needle = args.Length > 1 ? args[1] : "Spawner";
    var hits = provider.Files.Keys
        .Where(p => p.Contains(needle, StringComparison.OrdinalIgnoreCase)
                    && !p.Contains("_Generated_", StringComparison.OrdinalIgnoreCase))
        .OrderBy(p => p).ToList();
    Console.WriteLine($"{hits.Count:N0} paths containing '{needle}'");
    foreach (var h in hits.Take(200)) Console.WriteLine("  " + h);
    return;
}

if (mode == "dump")
{
    var path = args[1];
    var pkg = provider.LoadPackage(path);
    Console.WriteLine(JsonConvert.SerializeObject(pkg.GetExports(), Formatting.Indented));
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
