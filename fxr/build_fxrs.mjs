// Builds the custom generator flames: copies of the Coral generator's thruster FXRs (ThrustersSfxIdParam_PC column
// sfxId2) under new IDs, with every color whitened to its brightness and tinted by three custom external values
// that the hex_thrusters DLL provides (R, G, B).
// Usage: node build_fxrs.mjs <unpacked sfxbnd_commoneffects effect dir> [id map json out]
// Writes the new FXRs into the same dir, and optionally a {new id: source id} map (the new FXRs need the source's
// resource lists).
import {
  ActionType,
  ConstantProperty,
  ExternalValue1Modifier,
  FXR,
  Game,
  Keyframe,
  LinearProperty,
  ParticleModifier,
  ProxyNode,
} from '@cccode/fxr'
import fs from 'node:fs'
import path from 'node:path'

// Custom external values, served by the DLL as channel + 1 (1 = channel off, 2 = channel full). 0 means nothing
// provides them (no DLL), which the tint curve maps to "no tint", so the flames stay white instead of black.
const EXT_RED = 900001
const EXT_GREEN = 900002
const EXT_BLUE = 900003

// Coral flames (one per ThrustersSfxIdParam_PC row) -> new IDs at +1000, used as that row's sfxId3.
export const CORAL_FLAMES = [
  412000, 412001, 412002, 412003, 412010, 412011, 412012, 412013, 412021, 412031,
  432000, 432001, 432002, 432011, 442001, 442011, 442101, 452000, 452001, 452002,
]
// Base effects some Coral flames include as children. Other generators use them too, so they get copies at +9000.
const SHARED_CHILDREN = [410003, 410013, 410031, 430002]

const idMap = new Map([
  ...CORAL_FLAMES.map(id => [id, id + 1000]),
  ...SHARED_CHILDREN.map(id => [id, id + 9000]),
])

// Keeps brightness and alpha, drops the hue: the tint supplies it.
const whiten = ([r, g, b, a]) => {
  const m = Math.max(r, g, b)
  return [m, m, m, a]
}

// Multiplier for one channel: 0 (no DLL) -> 1, 1 -> 0, 2 -> 1, linear in between.
function channelTint(ext, channel) {
  const factor = v => [0, 1, 2, 3].map(i => (i === channel ? v : 1))
  return new ExternalValue1Modifier(ext, new LinearProperty(false, [
    new Keyframe(0, factor(1)),
    new Keyframe(1, factor(0)),
    new Keyframe(2, factor(1)),
  ]))
}

function addTint(action, key) {
  if ('field' in action.$data.props[key]) return 0 // fields can't carry modifiers
  if (Array.isArray(action[key])) {
    action[key] = new ConstantProperty(action[key])
  }
  action[key].modifiers.push(channelTint(EXT_RED, 0), channelTint(EXT_GREEN, 1), channelTint(EXT_BLUE, 2))
  return 1
}

// A particle's color is the product of several properties (the appearance's color1 x color2 x color3, the config's
// ParticleModifier color, ...), so the tint goes on exactly one factor: the ParticleModifier. Everything else that
// feeds into it is only whitened. Lights and lens flares aren't particles and get the tint on their own colors.
// Distortion colors stay as they are: they're neutral, and tinting them darkens the background into dark shapes.
function tintColors(fxr) {
  let tinted = 0
  for (const action of fxr.root.walkActions()) {
    if (!action.$data || !('props' in action.$data) || action.type === ActionType.Distortion) continue
    for (const [key, meta] of Object.entries(action.$data.props)) {
      if (!('color' in meta)) continue
      action.recolorProperty(key, whiten)
      if (action.type === ActionType.PointLight) {
        tinted += addTint(action, key)
      } else if (action.type === ActionType.LensFlare && /^layer\dColor$/.test(key)) {
        tinted += addTint(action, key)
      }
    }
  }
  for (const config of fxr.root.walkConfigs()) {
    const appearance = config.appearance
    if (!appearance?.$data?.isParticle || appearance.type === ActionType.Distortion) continue
    if (!config.particleModifier) {
      config.particleModifier = new ParticleModifier()
    }
    tinted += addTint(config.particleModifier, 'color')
  }
  return tinted
}

const effectDir = process.argv[2]
const file = id => path.join(effectDir, `f${String(id).padStart(9, '0')}.fxr`)
for (const [oldId, newId] of idMap) {
  // A path, not fs.readFileSync: small files come back as views into Node's shared buffer pool, which the library
  // reads from the pool's start.
  const fxr = await FXR.read(file(oldId), Game.ArmoredCore6)
  fxr.id = newId
  for (const node of fxr.root.walk()) {
    if (node instanceof ProxyNode && idMap.has(node.sfx)) {
      node.sfx = idMap.get(node.sfx)
    }
  }
  const tinted = tintColors(fxr)
  await fxr.saveAs(file(newId), Game.ArmoredCore6)
  console.log(`${oldId} -> ${newId}: ${tinted} colors tinted`)
}
if (process.argv[3]) {
  fs.writeFileSync(process.argv[3], JSON.stringify(Object.fromEntries([...idMap].map(([o, n]) => [n, o])), null, 1))
}
