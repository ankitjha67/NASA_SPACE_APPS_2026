"""Build the preset area file from geoBoundaries gbOpen releases.

Run once per catalog change:

    backend/.venv/Scripts/python scripts/build_boundaries.py India Pakistan Argentina

To add one country without downloading the others again:

    backend/.venv/Scripts/python scripts/build_boundaries.py --add Argentina

The output (backend/agnisetu/assets/boundaries.json) is committed, so the application itself
never contacts the boundary provider at run time.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import httpx
import shapely
from shapely.geometry import box, mapping, shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from agnisetu import firms  # noqa: E402
from agnisetu.aoi import geodesic_area_km2, normalize_geometry  # noqa: E402

API = "https://www.geoboundaries.org/api/current/{release}/{iso3}/{level}/"
# gbOpen is the default release. Its first level set for Argentina (2006) merges Entre Rios into
# Buenos Aires, so that level is taken from the humanitarian release, which uses the national
# survey (Instituto Geografico Nacional), the same source as the second level.
RELEASES = {("ARG", "ADM1"): "gbHumanitarian"}
# Preset areas are kept to continental Argentina and Isla Grande de Tierra del Fuego. The official
# set also carries Antarctic and South Atlantic territories, which are outside this analysis.
FRAMES = {"ARG": box(-74.0, -56.0, -53.0, -21.0).difference(box(-63.0, -53.6, -53.0, -50.0))}
OUT = ROOT / "backend" / "agnisetu" / "assets" / "boundaries.json"
TOLERANCE = {"ADM1": 0.006, "ADM2": 0.004}
ALLOWED_HOSTS = {"www.geoboundaries.org", "github.com", "raw.githubusercontent.com", "media.githubusercontent.com"}


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "unnamed"


def fetch_json(client: httpx.Client, url: str) -> dict:
    for _ in range(6):
        if httpx.URL(url).host not in ALLOWED_HOSTS:
            raise RuntimeError(f"Unexpected host for {url}")
        response = client.get(url)
        if response.status_code in (301, 302, 303, 307, 308):
            url = str(httpx.URL(url).join(response.headers["location"]))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("Too many redirects")


def clean_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return " ".join(folded.replace("_", " ").split()).title().replace(" And ", " and ").replace(" Of ", " of ")


# Misspelled unit names in the source release, corrected here. Keys are (ISO3, level, source name).
NAME_FIXES = {("ARG", "ADM1", "La Roja"): "La Rioja"}

# Countries whose unit names keep Spanish particles in lower case ("Tierra del Fuego").
SPANISH_NAMES = {"ARG"}

# Units that overlap polygons of another country's set. They stay in the analysis domain but are
# not offered as presets, so the list never shows two competing shapes for the same land.
EXCLUDED_PRESETS = {"adm1:PAK:azad-kashmir", "adm1:PAK:gilgit-baltistan"}


def main(countries: list[str], add: bool = False) -> None:
    features: list[dict] = []
    sources: list[dict] = []
    if add and OUT.exists():
        # Keep what is already built for every country that is not being rebuilt now.
        existing = json.loads(OUT.read_text(encoding="utf-8"))
        rebuilt = {firms.get_country(name) for name in countries}
        features = [f for f in existing["features"] if f["country"] not in {c.name for c in rebuilt}]
        sources = [s for s in existing.get("sources", []) if s["country"] not in {c.label for c in rebuilt}]
    with httpx.Client(timeout=120.0, follow_redirects=False) as client:
        for country_name in countries:
            country = firms.get_country(country_name)
            adm1_geoms: list[tuple[str, str, object]] = []
            for level in ("ADM1", "ADM2"):
                release = RELEASES.get((country.iso3, level), "gbOpen")
                meta = fetch_json(client, API.format(release=release, iso3=country.iso3, level=level))
                collection = fetch_json(client, meta["simplifiedGeometryGeoJSON"])
                sources.append(
                    {
                        "country": country.label,
                        "level": level,
                        "source": meta.get("boundarySource"),
                        "license": meta.get("boundaryLicense"),
                        "year": meta.get("boundaryYearRepresented"),
                        "boundary_id": meta.get("boundaryID"),
                    }
                )
                seen: dict[str, int] = {}
                for feature in collection["features"]:
                    raw = shape(feature["geometry"])
                    if country.iso3 in FRAMES:
                        raw = shapely.make_valid(raw).intersection(FRAMES[country.iso3])
                        if raw.is_empty or raw.area < 1e-6:
                            continue
                    geometry, _ = normalize_geometry(raw)
                    simplified = shapely.simplify(geometry, TOLERANCE[level], preserve_topology=True)
                    simplified, _ = normalize_geometry(simplified)
                    name = clean_name(feature["properties"].get("shapeName") or "Unnamed")
                    name = NAME_FIXES.get((country.iso3, level, name), name)
                    if country.iso3 in SPANISH_NAMES:
                        for particle in (" De La ", " De Los ", " De Las ", " Del ", " De ", " Y "):
                            name = name.replace(particle, particle.lower())
                    parent_id = None
                    parent_name = None
                    if level == "ADM1":
                        fid = f"adm1:{country.iso3}:{slug(name)}"
                    else:
                        point = geometry.representative_point()
                        for pid, pname, pgeom in adm1_geoms:
                            if pgeom.contains(point):
                                parent_id, parent_name = pid, pname
                                break
                        if parent_id is None and adm1_geoms:
                            # The two levels come from different surveys, so a small or coastal
                            # unit can fall just outside every parent. Use the parent it overlaps
                            # most, or the closest one when it overlaps none.
                            parent_id, parent_name, _ = max(
                                adm1_geoms,
                                key=lambda item: (item[2].intersection(geometry).area, -item[2].distance(geometry)),
                            )
                        parent_slug = parent_id.split(":")[-1] if parent_id else "unassigned"
                        fid = f"adm2:{country.iso3}:{parent_slug}:{slug(name)}"
                    seen[fid] = seen.get(fid, 0) + 1
                    if seen[fid] > 1:
                        fid = f"{fid}-{seen[fid]}"
                    if level == "ADM1":
                        adm1_geoms.append((fid, name, geometry))
                    listed = fid not in EXCLUDED_PRESETS and parent_id not in EXCLUDED_PRESETS
                    west, south, east, north = simplified.bounds
                    features.append(
                        {
                            "id": fid,
                            "name": name,
                            "level": "state" if level == "ADM1" else "district",
                            "listed": listed,
                            "country": country.name,
                            "country_label": country.label,
                            "parent_id": parent_id,
                            "parent_name": parent_name,
                            "bbox": [round(west, 4), round(south, 4), round(east, 4), round(north, 4)],
                            "area_km2": round(geodesic_area_km2(simplified), 1),
                            "geometry": json.loads(shapely.to_geojson(shapely.set_precision(simplified, 0.0001))),
                        }
                    )
                print(f"{country.label} {level}: {len(collection['features'])} units")
    # Drop anything that collapsed during precision reduction.
    features = [f for f in features if f["geometry"].get("coordinates")]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": 1, "sources": sources, "features": features}
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB, {len(features)} features)")
    _ = mapping  # keep the import explicit for readers extending the script


if __name__ == "__main__":
    arguments = [a for a in sys.argv[1:] if a != "--add"]
    main(arguments or ["India", "Pakistan", "Argentina"], add="--add" in sys.argv[1:])
