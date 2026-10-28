# Live tracking and recording signs

Your webcam can move the Blender figure in real time: fingers, hands, arms, head, eyebrows,
mouth, cheeks and eyes. You can record a sign, trim it, describe it, and save it as an animated
GIF card that shows up in the web app.

```
webcam -> web app, Record tab (MediaPipe tracking, in your browser)
       -> local connection on your own computer (127.0.0.1, port 8766)
       -> Blender add-on (turns the tracked points into joint rotations and moves the figure)
```

No video leaves your computer. The browser sends Blender only numbers (about 4 KB per frame).

## What you need

- Blender 4.2 or newer with the ASL Flashcard Editor add-on **version 0.5 or newer**.
- Chrome, Edge or Firefox, and a webcam.
- The web app opened from `http://localhost:8000` (see the README) or from GitHub Pages.
  Browsers only allow the camera on `localhost` or `https` addresses.

## Start

1. In Blender, press **N**, open the **ASL Cards** tab. If there is no figure yet, click
   **New Card Scene**.
2. In the **Live Tracking & Recording** panel, click **Start Live Link**.
3. In the web app, open the **Record** tab and click **Start camera**. The first time, the
   browser asks for camera permission and downloads the tracking models (about 20 MB).
4. The Status box turns green when the page finds Blender. Move your hands: the figure follows.
5. Face the camera with a relaxed face for a moment. The link calibrates itself when it first
   connects. Click **Calibrate** any time to redo it (for example after moving the camera).

Tips for good tracking:

- Light your face and hands from the front; avoid a bright window behind you.
- Sit so your head, shoulders and both hands fit in the picture, about an arm's length away.
- Plain clothes that contrast with your skin help the hand tracker.
- In Blender, use **Solid** viewport shading while tracking. It redraws fastest.

## Speed

The Status box shows the delay from the camera to the figure, split into tracking time (in
the browser) and Blender time (sending plus applying a frame). Tracking speed depends a lot on
the computer; with a graphics card it is usually a few tens of milliseconds per frame. Blender's
part is small: turning one frame into joint rotations and applying them took under 1 ms in
testing. The rest of the total is the camera itself.

What the app does to keep the delay low:

- It processes each camera frame as soon as it arrives (`requestVideoFrameCallback`).
- MediaPipe runs on the GPU, and in video mode, so hands are tracked from frame to frame
  instead of being found again each time.
- **Face tracking: Automatic** runs the face model every 2nd or 3rd frame when a computer is
  slow. Expressions change more slowly than hands.
- **Smoothing** uses a One Euro filter: it removes jitter when you hold still and follows
  quickly when you move. Set it to *off* for the rawest, fastest response.
- If Blender falls behind, old frames are dropped, so the figure always shows the newest pose.
- In Blender, turning the points into joint rotations takes well under 1 ms per frame.

## Recording a sign

1. Click **Record sign** (or press **R**). A countdown gives you time to get your hands up.
2. Sign. Recording stops by itself 1 second after both hands drop out of view, or press
   **Space** / click **Stop**. A recording can be up to 30 seconds long.
3. **Review**: the take is trimmed automatically to when your hands were visible. Drag the
   two yellow handles to cut more off the start or end. The figure in Blender jumps to the
   moment you're on, and the small picture shows what your camera saw then.
4. **Play** (or **Space**) plays the trimmed take on the figure.
5. Type **what the sign means**. This text is printed on the GIF (like the cards) and becomes
   the card's name in the app.
6. Choose the options and click **Make GIF**:
   - **Category**: word or phrase.
   - **Look**: *Fast* (simple shading with outlines; quick) or *Card look* (the card renderer;
     slower).
   - **Frame rate** and **Size**.
7. The GIF appears on the page with a **Download GIF** button.
8. Not happy? **Retry** (or **R**) throws the take away and starts a new countdown.
   **Discard** goes back to live tracking.

Everything can also be done from Blender's **Live Tracking & Recording** panel: Record Sign,
Stop, the Start/End trim fields, Play, Retry, Discard, Description and Make GIF.

### Where GIFs are saved

With the **Cards folder** set in the Card panel (pointing at this project's `cards` folder):

- `cards/motion/<category>-<name>.gif`: with the description on it
- `cards/motion/quiz/<category>-<name>.gif`: without it, for practice mode
- `cards/motion/<category>-<name>.json`: the recorded movement (every joint over time)

The card is added to `cards/manifest.json`, so it appears in the Cards tab and in practice
with a "moving" tag. If a card with the same name exists, the new one replaces it in the app.
The old files stay on disk.

## Settings

| Setting | What it does |
| --- | --- |
| Show the camera like a mirror | Flips only the preview on the page |
| Figure mirrors me | The figure's right hand follows your left hand. Useful for left-handed signers who want right-handed cards |
| Stop recording when my hands drop | Auto-stop after 1 second without hands |
| Countdown | 0, 2, 3 or 5 seconds before recording starts |
| Smoothing | Off, light, medium or strong jitter removal |
| Face tracking | Automatic, every frame, every other frame, or off |
| Blender port | Must match the Port in Blender's panel (default 8766) |

In Blender you can also switch tracking of **Arms, Hands, Head, Face and Body** on or off.
For example, to pose the arms with sliders while your hands drive the fingers.

**Copy Pose to Sliders** freezes the current live pose into the figure's sliders, so you can
fine-tune it and export a still card with **Export Card PNG**.

## Troubleshooting

- **"Not found. Is Start Live Link on?"**: start the link in Blender, and check both sides use
  the same port. Blender only listens while its panel shows "Waiting for the web app" or
  "Web app connected".
- **The page is on GitHub Pages and won't connect**: some browsers ask for permission before
  a website may talk to programs on your computer. Allow it, or run the web app locally.
- **Hands swap or flicker**: make sure your shoulders are visible. Hands are matched to the
  body's wrists.
- **The head is tilted when you're not**: click **Calibrate** while facing the camera.
- **High delay**: the Status box shows whether tracking or Blender is slow. Close other
  camera apps, set Face tracking to Automatic, and use Solid shading in Blender.

## How it works (for reading the code)

- Browser: `js/record/` (`mediapipe.js` loads the models, `tracker.js` runs them and builds
  messages, `oneeuro.js` smooths, `link.js` talks to Blender, `page.js` is the Record tab).
- Blender: section 15 of `blender/asl_flashcard_editor.py`:
  - `LiveLink`: a small WebSocket server using only Python's standard library.
  - `Retargeter`: landmarks to joint rotations. Each joint's rotation is solved from the
    tracked bone directions and the figure's own rest pose, so it is exact for the figure.
    It also covers the tricky cases: fists, where the knuckles bend to 90°; thumbs bent
    slightly backwards; and forearm twists past 180°.
  - `Take`: a recording. `render_take` and `encode_gif` render the trimmed take and write
    the animated GIF in pure Python.
