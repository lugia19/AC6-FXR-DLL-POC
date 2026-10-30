"""Builds the mod from an installed copy of Armored Core VI 1.09.1.

    python build.py --game "<...>/ARMORED CORE VI FIRES OF RUBICON/Game" --witchy "<...>/WitchyBND.exe"

Needs node/npm, cargo (stable) and WitchyBND 3.x. Writes package/: the me3 profile, the mod folder and the DLL.
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "build")
PACKAGE = os.path.join(ROOT, "package")

GENERATOR_ID = 65030100  # IB-C03G: NGI 000 (player version; the NPC one, 65902000, is left alone)
CORAL_COLOR_TYPE = 2
CUSTOM_COLOR_TYPE = 3    # thrustersColorType N uses ThrustersSfxIdParam_PC column sfxIdN; 3-7 are unused
FLAME_ID_OFFSET = 1000   # must match fxr/build_fxrs.mjs

FIELD_SIZES = {'s8': 1, 'u8': 1, 'dummy8': 1, 's16': 2, 'u16': 2, 's32': 4, 'u32': 4, 'f32': 4, 'angle32': 4,
               'b32': 4, 'f64': 8, 'fixstr': 1, 'fixstrW': 2}
FIELD_FORMATS = {'s8': '<b', 's32': '<i'}


class Param:
    """Raw .param file, fields located through WitchyBND's Paramdex defs, edited in place."""

    def __init__(self, path, def_path):
        self.path = path
        self.data = bytearray(open(path, "rb").read())
        self.fields, offset, bit_size, bits_used = {}, 0, None, 0
        for field in ET.parse(def_path).getroot().iter("Field"):
            typ, name, bits, count = re.match(r"(\w+)\s+(\w+)(?::(\d+))?(?:\[(\d+)\])?", field.get("Def")).groups()
            size = FIELD_SIZES[typ]
            if bits:  # bitfields share a storage unit until it's full
                if bit_size == size and bits_used + int(bits) <= size * 8:
                    bits_used += int(bits)
                    continue
                bit_size, bits_used = size, int(bits)
                offset += size
                continue
            bit_size = None
            self.fields[name] = (offset, typ)
            offset += size * (int(count) if count else 1)
        row_count = struct.unpack_from("<H", self.data, 0xA)[0]
        self.rows = dict(struct.unpack_from("<i4xQ", self.data, 0x40 + i * 0x18) for i in range(row_count))

    def get(self, row, field):
        offset, typ = self.fields[field]
        return struct.unpack_from(FIELD_FORMATS[typ], self.data, self.rows[row] + offset)[0]

    def set(self, row, field, value):
        offset, typ = self.fields[field]
        struct.pack_into(FIELD_FORMATS[typ], self.data, self.rows[row] + offset, value)

    def save(self):
        open(self.path, "wb").write(self.data)


def unpacked(path):
    # WitchyBND's folder naming: regulation.bin -> regulation-bin, x.ffxbnd.dcx -> x-ffxbnd-dcx-wffxbnd
    name = os.path.basename(path)
    return os.path.join(WORK, name.replace(".", "-") + ("-wffxbnd" if name.endswith(".ffxbnd.dcx") else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", required=True, help="the game's Game folder (with regulation.bin and sfx/)")
    parser.add_argument("--witchy", required=True, help="WitchyBND.exe (3.x)")
    args = parser.parse_args()
    defs = os.path.join(os.path.dirname(args.witchy), "Assets", "Paramdex", "AC6", "Defs")

    # WitchyBND exits 0 even when it does nothing, so check the results.
    def unpack(path):
        subprocess.run([args.witchy, "-s", path], check=True)
        assert os.path.isdir(unpacked(path)), f"WitchyBND didn't unpack {path}"

    def repack(path):
        before = os.path.getmtime(path)
        subprocess.run([args.witchy, "-s", unpacked(path)], check=True)
        assert os.path.getmtime(path) != before, f"WitchyBND didn't repack {path}"

    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    regulation = shutil.copy(os.path.join(args.game, "regulation.bin"), WORK)
    effects = shutil.copy(os.path.join(args.game, "sfx", "sfxbnd_commoneffects.ffxbnd.dcx"), WORK)
    unpack(regulation)
    unpack(effects)

    # Custom flames: copies of the Coral ones, tinted by the DLL's external values.
    fxr_dir = os.path.join(ROOT, "fxr")
    shell = os.name == "nt"
    if not os.path.isdir(os.path.join(fxr_dir, "node_modules")):
        subprocess.run(["npm", "install"], cwd=fxr_dir, check=True, shell=shell)
    effect_dir = os.path.join(unpacked(effects), "effect")
    id_map_path = os.path.join(WORK, "id_map.json")
    subprocess.run(["node", "build_fxrs.mjs", effect_dir, id_map_path], cwd=fxr_dir, check=True, shell=shell)
    # The new FXRs use their sources' textures and models. WitchyBND would generate empty resource lists for them.
    reslists = os.path.join(unpacked(effects), "ResourceList")
    for new_id, source_id in json.load(open(id_map_path)).items():
        shutil.copy(os.path.join(reslists, f"f{source_id:09d}.ffxreslist"), os.path.join(reslists, f"f{int(new_id):09d}.ffxreslist"))

    # Params: the generator gets the new color type, and every thruster effect row its custom flame in that column.
    params = unpacked(regulation)
    generators = Param(os.path.join(params, "EquipParamGenerator.param"), os.path.join(defs, "EquipParamGenerator.xml"))
    assert generators.get(GENERATOR_ID, "thrustersColorType") == CORAL_COLOR_TYPE
    generators.set(GENERATOR_ID, "thrustersColorType", CUSTOM_COLOR_TYPE)
    generators.save()
    thrusters = Param(os.path.join(params, "ThrustersSfxIdParam_PC.param"), os.path.join(defs, "ThrustersSfxIdParam.xml"))
    for row in thrusters.rows:
        coral = thrusters.get(row, f"sfxId{CORAL_COLOR_TYPE}")
        if coral != -1:
            assert os.path.exists(os.path.join(effect_dir, f"f{coral + FLAME_ID_OFFSET:09d}.fxr"))
            thrusters.set(row, f"sfxId{CUSTOM_COLOR_TYPE}", coral + FLAME_ID_OFFSET)
    thrusters.save()

    repack(regulation)
    repack(effects)
    subprocess.run(["cargo", "+stable", "build", "--release"], cwd=os.path.join(ROOT, "dll"), check=True)

    shutil.rmtree(PACKAGE, ignore_errors=True)
    os.makedirs(os.path.join(PACKAGE, "mod", "sfx"))
    shutil.copy(regulation, os.path.join(PACKAGE, "mod"))
    shutil.copy(effects, os.path.join(PACKAGE, "mod", "sfx"))
    shutil.copy(os.path.join(ROOT, "dll", "target", "release", "hex_thrusters.dll"), PACKAGE)
    shutil.copy(os.path.join(ROOT, "hex-thrusters.me3"), PACKAGE)
    print(f"Built {PACKAGE}")


if __name__ == "__main__":
    main()
