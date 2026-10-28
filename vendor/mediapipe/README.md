# Offline copy of MediaPipe (optional)

The Record tab normally downloads Google's MediaPipe tracking library and models from the
internet the first time you start the camera (about 20 MB, then cached by the browser).

To run fully offline, put the files here and the app uses them instead:

```
vendor/mediapipe/
  vision_bundle.mjs            from the npm package @mediapipe/tasks-vision@0.10.35
  wasm/                        the package's wasm folder (4 files)
  models/hand_landmarker.task
  models/pose_landmarker_lite.task
  models/face_landmarker.task
```

Get the library with `npm pack @mediapipe/tasks-vision@0.10.35` (or from
https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/), and the models from
https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker (and the pose and face
landmarker pages). These files are not committed to the repository (see .gitignore).
