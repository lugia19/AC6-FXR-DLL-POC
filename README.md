# AC6-FXR-DLL-POC

Proof of concept for Armored Core VI (1.09.1): a DLL feeds **custom external values** into FXRs, so effects can
react to any data a mod has. Here, you type a hex color in game and your thruster flames take that color.

The example is deliberately simple. The same approach works for any value a DLL can get (game state, input, a
file, the network), driving any color, size, speed or state switch in an effect.

## Try it

1. Download the prebuilt zip from the [Releases](../../releases) page and extract it anywhere.
2. Launch `hex-thrusters.me3` with [me3](https://github.com/garyttierney/me3) (ModEngine 3). It loads `mod/` and
   `hex_thrusters.dll`.
3. Equip the **IB-C03G: NGI 000** generator.
4. In game: **F8**, then **6 hex digits** (0-9 on the top row or numpad, A-F). The color applies after the 6th
   digit. Backspace deletes, Esc or F8 again cancels, 10 s without input cancels. Default: `#FF3030`.

Typing A, D or F also reaches the game, so the AC may move while you type. The DLL logs to
`Game\debug-logs\hex_thrusters.log`.

Only ACs with this generator get the custom flames: the NPC version of NGI 000 and the other Coral generator keep
their stock flames.

## How it works

### External values

FXR modifiers and state conditions can read **external values**: numbered floats the game supplies to an effect.
The code that spawns an effect provides them, and an effect only gets the IDs its FXR header declares (anything
else reads as 0). Every read goes through one function, `0x142b56480` in 1.09.1:
`float *get(value_block, float *out, int id)`. Details:
[AC6-FXR-External-values-research](https://github.com/lugia19/AC6-FXR-External-values-research).

So an FXR can declare an ID the game never uses (here **900001/900002/900003** for red/green/blue), and a DLL
hooking that function can answer it. The DLL here only answers those three IDs and leaves every other read to the
game.

### The mod

- **Generator color type.** `EquipParamGenerator.thrustersColorType` picks a column of `ThrustersSfxIdParam_PC`:
  type N uses `sfxIdN` (0 = 410xxx, 1 = 411xxx, 2 = 412xxx Coral). Columns 3-7 are unused, so IB-C03G: NGI 000 gets
  type 3 and every thruster effect row gets a custom flame in `sfxId3`.
- **Flames** (`fxr/build_fxrs.mjs`, with [@cccode/fxr](https://github.com/EvenTorset/fxr)): copies of the 20 Coral
  flames at +1000 (413xxx, 433xxx, 443xxx, 453xxx), plus copies of four shared base effects some of them include
  (+9000), with child references remapped. Every color is whitened to its brightness. The typed color multiplies
  in through `ExternalValue1Modifier`s on 900001-900003, **once per particle**: on each config's ParticleModifier
  color, since a particle's color is the product of its appearance colors and that one. (Tinting every color
  property applies the tint several times and turns most colors black.) Lights and lens flare layers are tinted
  directly; distortion is left alone.
- **DLL** (`dll/`, Rust): hooks the read function, answers 900001-900003 with `channel / 255 + 1`, and reads the
  keyboard while the game window is focused. The `+1` keeps 0 meaning "no value": the tint curves map 0 to white,
  so without the DLL the flames are white instead of black. The DLL checks the function's first bytes and does
  nothing on other game versions.

## Build

```
python build.py --game "<...>/ARMORED CORE VI FIRES OF RUBICON/Game" --witchy "<...>/WitchyBND.exe"
```

Needs Python 3, node/npm, cargo (stable) and [WitchyBND](https://github.com/ividyon/WitchyBND) 3.x. It takes
`regulation.bin` and `sfx/sfxbnd_commoneffects.ffxbnd.dcx` from the game, generates the flames, patches the two
params, repacks, builds the DLL, and writes `package/` (what the release zip contains). Each new FXR gets its
source's resource list, since WitchyBND would generate empty ones.
