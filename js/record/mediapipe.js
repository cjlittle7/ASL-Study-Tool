// Loads Google's MediaPipe Tasks (hand, pose and face landmarkers) and runs them on
// every new camera frame. Everything runs in this browser tab, on the GPU when available;
// no video leaves the computer.
//
// Offline use: put the files in vendor/mediapipe/ (see vendor/mediapipe/README.md) and
// they are used instead of the internet copies.

export const MP_VERSION = "0.10.35";
const CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}/`;
const LOCAL = new URL("../../vendor/mediapipe/", import.meta.url).href;
const MODEL_FILES = {
  hand: "hand_landmarker.task",
  pose: "pose_landmarker_lite.task",
  face: "face_landmarker.task",
};
const MODEL_URLS = {
  hand: "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
  pose: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
  face: "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
};

async function exists(url) {
  try {
    const r = await fetch(url, { method: "HEAD", cache: "no-store" });
    return r.ok;
  } catch {
    return false;
  }
}

/** Load the three landmarkers. onStatus(text) reports progress. */
export async function loadLandmarkers(onStatus = () => {}) {
  onStatus("Loading the tracking library...");
  const local = await exists(LOCAL + "vision_bundle.mjs");
  const base = local ? LOCAL : CDN;
  const vision = await import(/* @vite-ignore */ base + "vision_bundle.mjs");
  const fileset = await vision.FilesetResolver.forVisionTasks(base + "wasm");

  const modelUrl = async (k) => ((await exists(LOCAL + "models/" + MODEL_FILES[k])) ? LOCAL + "models/" + MODEL_FILES[k] : MODEL_URLS[k]);
  const urls = { hand: await modelUrl("hand"), pose: await modelUrl("pose"), face: await modelUrl("face") };

  async function create(Klass, opts, label) {
    onStatus(`Loading the ${label} model...`);
    for (const delegate of ["GPU", "CPU"]) {
      try {
        const lm = await Klass.createFromOptions(fileset, { ...opts, baseOptions: { ...opts.baseOptions, delegate } });
        return { lm, delegate };
      } catch (err) {
        if (delegate === "CPU") throw err;
      }
    }
  }

  const hand = await create(vision.HandLandmarker, {
    baseOptions: { modelAssetPath: urls.hand }, runningMode: "VIDEO", numHands: 2,
    minHandDetectionConfidence: 0.5, minHandPresenceConfidence: 0.5, minTrackingConfidence: 0.5,
  }, "hand");
  const pose = await create(vision.PoseLandmarker, {
    baseOptions: { modelAssetPath: urls.pose }, runningMode: "VIDEO", numPoses: 1,
  }, "body");
  const face = await create(vision.FaceLandmarker, {
    baseOptions: { modelAssetPath: urls.face }, runningMode: "VIDEO", numFaces: 1,
    outputFaceBlendshapes: true, outputFacialTransformationMatrixes: false,
  }, "face");
  onStatus("");
  return { hand: hand.lm, pose: pose.lm, face: face.lm, delegate: hand.delegate, local };
}
