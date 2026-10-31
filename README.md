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

### Custom external values

FXR modifiers and state conditions can read **external values**: numbered floats the game supplies to an effect.
Normally the code that spawns an effect provides them. An effect gets a slot for every ID its FXR header declares,
initialized to 0, and every read goes through one function. Background:
[AC6-FXR-External-values-research](https://github.com/lugia19/AC6-FXR-External-values-research).

A custom value takes two parts:

**1. The FXR reads an ID the game never uses.** Pick a reserved range (the game's own IDs stop at 70200; this
POC uses 900001-900003) and read it in a modifier or a state condition. With
[@cccode/fxr](https://github.com/EvenTorset/fxr), the ID ends up in the header's declaration list automatically:

```js
// Scales a property with value 900001: 0 -> x0, 1 -> x1 (linear, clamped).
prop.modifiers.push(new ExternalValue1Modifier(900001, new LinearProperty(false, [
  new Keyframe(0, 0), new Keyframe(1, 1),
])))
// Or switch states: State.from('ext(900001) >= 1 then 1')
```

Use `ExternalValue1Modifier`: it's evaluated live. `ExternalValue2` values are only read when the effect is
created.

**2. The DLL answers it.** Effects read values through `0x142b56480` (1.09.1), effectively
`float *get(value_block, float *out, int id)`: it looks `id` up in the effect's value block and writes the value,
or 0 if nobody provides it. The DLL detours that function, lets the game's lookup run, then overwrites the result
for its own IDs only:

```rust
unsafe extern "system" fn get_impl(block: *mut u8, out: *mut u32, id: i32) -> *mut u32 {
    let result = orig(block, out, id);      // the game's lookup, unchanged for every other ID
    if (900001..=900003).contains(&id) {
        *out = my_value(id).to_bits();      // the float, written as raw bits
    }
    result
}
```

What the game's function does (Ghidra decompile of `0x142b56480`, cleaned up):

```cpp
struct ValueBlock {     // one per effect instance, 0x20 bytes
    void*  unknown;     // +0x00
    int*   ids;         // +0x08  external value IDs, sorted ascending
    float* values;      // +0x10  values[i] belongs to ids[i]
    uint32 count;       // +0x18
};

float* get(ValueBlock* block, float* out, int id) {
    float result = 0.0f;                                // the default for IDs the block doesn't have
    int* end = block->ids + block->count;
    int* it = std::lower_bound(block->ids, end, id);    // binary search
    if (it != end && *it == id) {
        result = block->values[it - block->ids];
    }
    *out = result;
    return out;                                         // the pointer it was given, not the value
}
```

That's all it does: a lookup in two parallel arrays. The game fills an effect's block when the effect is spawned
or updated (the spawner sets its values, which get copied into the instance's block), and every modifier or state
condition that reads an external value calls this with the effect's block and the ID from the FXR. Since the
lookup has no side effects, a hook can also skip it for its own IDs: write the value to `*out` and return `out`.
(The real function also fills its stack frame with `0xCCCCCCCC` and calls `_RTC_CheckStackVars`, MSVC's runtime
stack checks, normally only seen in debug builds. They don't affect the result.)

The rest of `dll/src/lib.rs` is setup and this mod's input:

- **Hooking:** a `retour::RawDetour` on the function; the trampoline is the `orig` above. Before hooking, the DLL
  checks the function's first bytes (`44 89 44 24 18`, `mov [rsp+18],r8d`) and does nothing if they don't match,
  e.g. on another game version. It installs the hook in `DllMain` when me3 loads it as a native.
- **The value source has to be cheap and thread-safe.** The function is called for every external value read by
  every effect, many times per frame and from effect update threads. Here the color lives in an `AtomicU32`, set
  by a keyboard polling thread.
- **Values are global:** the hook gets the effect's value block, not the effect, so every effect reading 900001
  gets the same value. Per-effect values would mean mapping blocks to effects (the research repo's logger does
  this by hooking the attach function).
- **0 means "no value".** Without the DLL every custom ID reads as 0, so an effect should look sensible at 0.
  This POC serves `channel / 255 + 1` and its curves map 0 to white, so without the DLL the flames are white
  instead of black.
- **Porting to another game version:** find the function again (byte pattern of its body, or via its callers in
  the modifier evaluators) and update the address.

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
- **DLL** (`dll/`, Rust): the hook above for 900001-900003 (red, green, blue), plus a thread that polls the
  keyboard (`GetAsyncKeyState`) while the game window is focused and stores the typed color.

## Build

```
python build.py --game "<...>/ARMORED CORE VI FIRES OF RUBICON/Game" --witchy "<...>/WitchyBND.exe"
```

Needs Python 3, node/npm, cargo (stable) and [WitchyBND](https://github.com/ividyon/WitchyBND) 3.x. It takes
`regulation.bin` and `sfx/sfxbnd_commoneffects.ffxbnd.dcx` from the game, generates the flames, patches the two
params, repacks, builds the DLL, and writes `package/` (what the release zip contains). Each new FXR gets its
source's resource list, since WitchyBND would generate empty ones.
