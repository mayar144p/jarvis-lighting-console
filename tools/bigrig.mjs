// The big test rig: 124 lights of 19 real models (moving heads, washes,
// beams, PARs, Source Four LEDs, pixel bars, strobes, effect lights,
// lasers, foggers), shared by vischeck.mjs and uicheck.mjs --big.
// Tests use one of each model (20 lights) to save time; BIG_RIG=1 builds
// all 124.  [library, file, what add_heads searches for, how many]
const FULL = [
  ["qlc", "Martin/Martin-MAC-Aura.qxf", "MAC Aura", 8],
  ["qlc", "Clay_Paky/Clay-Paky-Sharpy-Plus.qxf", "Sharpy Plus", 8],
  ["qlc", "Chauvet/Chauvet-Rogue-R2-wash.qxf", "Rogue R2 Wash", 8],
  ["qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf", "Intimidator Wave 360", 2],
  ["qlc", "Nicols/Nicols-Moover-Spot-120.qxf", "Moover Spot 120", 4],
  ["qlc", "American_DJ/American-DJ-Mega-PAR-Profile-Plus.qxf", "Mega PAR Profile Plus", 16],
  ["qlc", "Chauvet/Chauvet-COLORado-1-Quad-Zoom-Tour.qxf", "COLORado 1 Quad", 12],
  ["qlc", "American_DJ/American-DJ-Flat-Par-QA12X.qxf", "Flat Par QA12X", 12],
  ["ofl", "eurolite/led-par-56-tcl.json", "LED PAR-56 TCL", 12],
  ["qlc", "ETC/ETC-s4-Lustr2.qxf", "Lustr", 8],
  ["qlc", "Showtec/Showtec-Sunstrip-Active.qxf", "Sunstrip Active", 6],
  ["qlc", "Showtec/Showtec-Pixel-Bar-12-RGBW.qxf", "Pixel Bar 12 RGBW", 8],
  ["qlc", "Martin/Martin-Atomic-3000-LED.qxf", "Atomic 3000", 4],
  ["qlc", "Chauvet/Chauvet-Swarm-Wash-FX.qxf", "Swarm Wash FX", 4],
  ["qlc", "Chauvet/Chauvet-Freedom-Par-Hex-4.qxf", "Freedom Par Hex 4", 8],
  ["qlc", "Laserworld/Laserworld-RS400G.qxf", "RS400G", 1],
  ["jarvis", "laserworld/beambar-10b-mk3", "BeamBar 10B", 1],
  ["qlc", "Stairville/Stairville-AF-180-LED-Fogger-Co2-FX.qxf", "AF-180", 1],
  ["qlc", "Chauvet/Chauvet-Hurricane-1800-Flex.qxf", "Hurricane 1800", 1],
];
export const RIG = process.env.BIG_RIG ? FULL : FULL.map(([l, f, n, q], i) => [l, f, n, i === 0 ? 2 : 1]);
