// Which GPU path the 3D views take (WebGPU, or WebGL 2), asked once.

// Can this browser run three.js's WebGPU path?  Asked once, before the
// first view is made: a WebGPU device that also takes the texture options
// this three.js uses (an older Chromium has WebGPU but rejects them).  The
// device that passed is the one the views then use (a second one, or
// destroying this one, can lose the GPU).  No device: the same renderer
// runs on WebGL 2.  localStorage jarvis.renderer = "webgl" forces WebGL 2.
async function probeWebGPU() {
  try {
    if (localStorage.getItem("jarvis.renderer") === "webgl") return null;
  } catch (e) { /* no storage */ }
  if (!navigator.gpu) return null;
  const timeout = new Promise((ok) => setTimeout(() => ok(null), 2500));
  const probe = (async () => {
    try {
      const adapter = await navigator.gpu.requestAdapter({ powerPreference: "high-performance" });
      if (!adapter) return null;
      // the limits and features three.js asks for, so it can use this device
      const features = [...adapter.features].filter((f) => adapter.features.has(f));
      const limits = {};
      for (const k in adapter.limits) if (typeof adapter.limits[k] === "number") limits[k] = adapter.limits[k];
      const device = await adapter.requestDevice({ requiredFeatures: features, requiredLimits: limits });
      const tex = device.createTexture({ size: [1, 1], format: "rgba8unorm", usage: 4 /* TEXTURE_BINDING */ });
      tex.createView({ swizzle: "rgba" });
      tex.destroy();
      return device;
    } catch (e) {
      return null;
    }
  })();
  return Promise.race([probe, timeout]);
}
export const GPU_DEVICE = await probeWebGPU();
export const rendererOpts = (o) => (GPU_DEVICE ? { ...o, device: GPU_DEVICE } : { ...o, forceWebGL: true });
