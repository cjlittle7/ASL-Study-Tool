# Live tracking and recording signs

Your webcam can move the Blender figure in real time: fingers, hands, arms, head, eyebrows,
mouth, cheeks and eyes. You can record a sign, trim it, describe it, add arrows, and save it as
an animated GIF card that shows up in the web app.

```
webcam -> web app, Record tab (MediaPipe tracking, in your browser)
       -> local connection on your own computer (127.0.0.1, port 8766)
       -> Blender add-on (turns the tracked points into joint rotations and moves the figure)
```

No video leaves your computer. The browser sends Blender only numbers (about 4 KB per frame).

## What you need

- Blender 4.2 or newer with the ASL Flashcard Editor add-on **version 0.8 or newer**.
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

## Hands near the face and body

When your hand touches your nose, the figure's hand should touch the figure's nose. A webcam
can't see depth directly, and MediaPipe's own depth for the arms is rough, so the add-on works
out where each hand really is:

1. **How far away things are.** Your face's distance follows from how far apart your eye
   corners are in the picture. Each hand's distance follows from its size in the picture. This
   uses the camera's **field of view** (see Settings).
2. **Touching.** When a hand overlaps your face in the picture and is about as far away as your
   face, it is touching. The part of the hand that reaches furthest toward the face (usually a
   fingertip) is put exactly on the skin. A hand just in front of the chest is treated the same way.
3. **Your body onto the figure's body.** Near the face, points are carried over using about 40
   spots on the face (nose tip, chin, lips, cheeks, eyebrows, temples...). These spots are found
   on the figure's own head, so this still works after you resize or sculpt the head. Elsewhere,
   points are carried over relative to the shoulders and scaled to the figure's size, so someone
   with longer arms than the figure still reaches the same places.
4. **Arms.** The wrist goes where the figure's hand best covers your hand, with the touching
   fingertip counting most. The shoulder and elbow follow, and the elbow points the way yours does.
   Hands are kept out of the head and chest.

The Status box shows **Touching** when a hand is taken as touching the face or chest. Turn
**Place hands by position** off to go back to copying only the arm directions.

**Camera field of view** is measured automatically: as your hands move, their nearer parts look
bigger by an amount that depends on how wide the lens is, and the add-on picks the width that
fits best. It takes a few seconds (pointing your fingers toward the camera now and then helps),
and the Settings box shows the result. You can still choose a value by hand: if hands land a
little in front of the face, choose a wider angle; if they sink into it, a narrower one.

When a hand touches the face, the add-on also learns how far off MediaPipe's hand size is for
you, which makes hand distance more accurate for the rest of the session.

## Handshape help

Fingers are the hardest part to track: a webcam often sees them half hidden, so a tracked V can
come out with a bent finger or the thumb in the wrong place. **Handshape help** knows the ASL
handshapes (A, S, B, 5, C, O, 1, L, V, W, I, Y, X, flat O, bent B, claw, D, E, F, G, K, M, N, R,
T, U/H and a relaxed hand) and guides the figure's fingers into the exact shape:

- A small machine-learning model (k-nearest neighbors) compares your tracked finger angles
  with examples of each handshape. It learns the examples by bending the figure's own hand into
  every handshape in many slightly different ways and tracking them, so it knows how each shape
  looks to the tracker. That takes about half a second when the link starts.
- When it's sure which handshape you're making, the fingers are pulled into that handshape's
  exact pose. When it isn't sure (in the middle of changing from one sign to the next, or a shape
  it doesn't know), the fingers are left as tracked. **Strength** sets how hard it pulls.
- The Status box shows what it sees, for example "right V 92%".

### Teaching it your hands

Everyone's hands are a little different, so you can teach it your own versions. In the Record
tab, open **Teach handshapes**, pick a handshape and a hand, click **Teach**, and hold the shape
for 3 seconds (turning your wrist a little gives it more to learn from). Your examples count
three times as much as the built-in ones and are saved in Blender's settings folder
(`asl_handshape_examples.json`), so they're kept for next time. The figure still makes the
*correct* handshape: your examples only help recognize it. **Forget all my examples** removes
them.

In testing with deliberately sloppy handshapes (every finger joint randomly off by about 9°, seen
from random wrist angles), it picked the right handshape about 93% of the time, and when it
wasn't sure it mostly left the fingers alone. One person's unusual V went from not recognized to
recognized every time after teaching it once.

## Crossed arms and turning wrists

Two kinds of tracking glitch used to make the figure jerk:

- **The tracker reads a hand inside out** for a frame (its depth backwards, so a right hand
  looks like a left one). The wrist then spins half a turn. The add-on now checks each hand's 3D
  shape: a right hand's thumb and bent fingers are always on one particular side of the palm,
  and a left hand's on the other. A hand that comes out mirror-imaged is flipped back.
- **The two hands get mixed up** when the arms cross. The web page now remembers where each
  hand was a moment ago and also uses MediaPipe's own left/right judgment, so hands keep their
  identity while they pass each other, even when the body tracker mixes up the wrists. A swap
  has to be clearly better before it happens.

As a last safety net, an arm joint that suddenly turns more than about 55° in one frame is held
back for up to 3 frames, and only follows if the new position is still there; real movements
that fast are rare.

In testing with these glitches added to wrist turns and crossed arms, the figure's joints jumped
more than 30° in one frame 30-34 times per run without the fixes, and not at all with them.


With one camera, how far away your hands are has to be estimated. A phone set up at another
angle sees the same points from the side, so Blender can measure it instead (triangulation).
This helps most with signs that move toward or away from the body, and with a hand hidden from
the laptop camera by the other hand.

What you need: the web app published on **GitHub Pages** (or another `https` address), because
phone browsers only allow the camera on `https` pages. The phone and laptop should be on the same
Wi-Fi.

1. On the laptop, in the Record tab, click **Connect a phone** (in the Phone camera box). A QR
   code and a 6-letter code appear.
2. On the phone, scan the QR code (or open the web app and go to `#/phone`, then type the code).
   Tap **Start phone camera** and allow the camera.
3. Stand the phone up 1 to 2 meters away, about a quarter turn (45-90°) from the laptop
   camera, where it sees your head and both hands. The back camera is sharper if you can
   see the screen some other way; the front camera lets you check the picture.
4. Move your arms around for a few seconds. Blender works out where the phone is and what its
   lens is from your movements; you don't measure anything. The box then says **In use**, with
   the angle between the cameras.

If the phone gets moved, its points stop lining up with the laptop's; Blender notices within a
couple of seconds and finds it again (move your arms around). **Find the phone again** forces this.

How the delay is kept low:

- The phone does its own tracking and sends only point positions (about 2 KB per frame), never video.
- The phone and laptop talk directly (WebRTC). A free public server is used only to introduce
  them with the code.
- Phone frames travel on a channel that never re-sends or queues: a late frame is dropped
  instead of holding up newer ones.
- The phone's clock is matched to the laptop's with quick time checks, and Blender
  interpolates the phone's points to the exact moment of each laptop frame, or predicts them a
  few hundredths of a second ahead. **Nothing waits for the phone**: the laptop camera still
  drives the figure at its own speed, and the phone corrects the depth.
- If the phone's frames stop or fall too far behind, Blender goes back to one camera smoothly.

Recordings keep the phone's points, so a take recorded with two cameras is solved with two
cameras again if you change the figure's body.

Later, with a second USB webcam: the same combining works for a second camera on the laptop
itself, without pairing. That option isn't in the app yet.

## Eyebrows

Each eyebrow has three points that move on their own: the inner end, the middle and the outer
end, plus a **squeeze** that pulls the inner ends together. That covers raised brows (yes/no
questions), furrowed brows (wh-questions), worried brows (inner ends up), one raised brow, and
so on. They're measured from the eyebrow points MediaPipe finds, compared with your relaxed face
from calibration, so calibrate with a relaxed face. The same controls are sliders in Blender's
**Body & Face** panel.

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

### Arrows on the GIF

In the Review panel, **Arrows on the GIF** adds movement arrows that are drawn into every frame.
Pick a kind and click **Add arrow**. Click an arrow's name to open its settings.

| Kind | What it does |
| --- | --- |
| Path of the movement | Traces where a body part (for example the index fingertip) moved, between the two times you choose. **Cut from the start / end** keeps it clear of the hand; **Draw the arrow as the hand moves** grows it along with the sign |
| Straight, on a body part | Points along an arm, hand or finger and moves with it |
| Circling a body part | Goes around a body part (for example the wrist, for a twist) and moves with it |
| Straight, arc, circle, back and forth, zigzag (fixed) | A shape placed near a body part. It stays put |

Every arrow can be moved left/right, up/down and back/front, shown for only part of the GIF
(**Show the arrow from / to**; **now** uses the moment shown in Blender), reversed, given
arrowheads at both ends, made thicker or thinner, and colored. A preview picture, rendered by
Blender at the current moment, updates after each change. The arrows also show in Blender's
viewport while you review, and **Retry** or **Discard** removes them.
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
| Place hands by position | Hands are placed relative to your face and body (see above). Off: only arm directions are copied |
| Camera field of view | How wide your webcam sees. **Automatic** (default) measures it from your hands; or pick it (most webcams are about 60°, wide ones about 78°) |
| Blender port | Must match the Port in Blender's panel (default 8766) |

In Blender you can also switch tracking of **Arms, Hands, Head, Face and Body** on or off.
For example, to pose the arms with sliders while your hands drive the fingers.

**Copy Pose to Sliders** freezes the current live pose into the figure's sliders, so you can
fine-tune it and export a still card with **Export Card PNG**.

## Changing the figure's body

The **Body Shape & Sculpting** panel in Blender changes the figure: head size, body width,
height and depth, upper arm and forearm length, arm thickness and hand size. You can also
sculpt any part (a bigger nose, a rounder head, a different chest) with Blender's sculpt
brushes. Live tracking follows these changes within a second: arm lengths, shoulder positions
and the face spots used for touching are read from the figure itself.

A take you have already recorded follows them too. Blender keeps the tracking data of the take,
and when the body has changed it solves the take again for the new body before playing it or
making the GIF, so a fingertip that touched the nose still touches the (new) nose.

## Troubleshooting

- **"Not found. Is Start Live Link on?"**: start the link in Blender, and check both sides use
  the same port. Blender only listens while its panel shows "Waiting for the web app" or
  "Web app connected".
- **The page is on GitHub Pages and won't connect**: some browsers ask for permission before
  a website may talk to programs on your computer. Allow it, or run the web app locally.
- **Hands swap or flicker**: make sure your shoulders are visible. Hands are matched to the
  body's wrists.
- **The head is tilted when you're not**: click **Calibrate** while facing the camera.
- **Eyebrows look raised or furrowed when relaxed**: click **Calibrate** with a relaxed face.
- **A hand near the face lands in front of or inside the figure's face**: give the automatic
  field of view a few seconds of hand movement, or choose a value by hand in the Record tab's
  Settings (or in Blender's panel).
- **The phone shows "404"**: the app isn't published on GitHub Pages yet, or the published copy
  is older than the phone camera feature. Publish it (see the README); the Record tab's Phone
  camera box shows a warning until it finds the online copy.
- **The phone won't pair**: both devices need internet for the pairing step. Make sure the laptop
  still shows the code, and that the phone opened the address from the QR code (an `https` address).
- **Phone connected but "Finding the phone" never finishes**: the phone must see your face and
  hands at the same time as the laptop does. Move your arms through a big area for a few seconds.
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
  - `DepthPlacer` and `ModelSkeleton`: hand placement by position (pinhole camera, face and
    chest contact, face anchors found by ray casts on the figure's head, two-bone arm solve).
  - Take arrows: `clean_take_arrow`, `build_take_arrows`, `update_take_arrows`.
  - `Take`: a recording. `render_take` and `encode_gif` render the trimmed take and write
    the animated GIF in pure Python.
