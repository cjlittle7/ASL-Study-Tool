# ASL Flashcard Editor: guide

The editor is a Blender add-on. It gives you a simple signer figure you pose with sliders,
then exports a square PNG flashcard with the English term printed at the bottom.

Works with Blender 4.2 or newer (tested with 5.2 LTS). Download Blender free from blender.org.

## Install (once)

1. Open Blender.
2. **Edit → Preferences → Add-ons**.
3. Click the small down arrow in the top-right corner of that window and choose **Install from Disk…**
4. Pick `blender/asl_flashcard_editor.py` from this project.
5. Make sure the checkbox next to **ASL Flashcard Editor** is ticked.

## Make your first card

1. In the 3D Viewport, press **N** to open the sidebar and click the **ASL Cards** tab.
2. Click **New Card Scene**. A figure, camera, lights and label appear.
3. Click **Camera View** to see exactly what the card will look like (or press `Numpad 0`).
4. Type the **English term**, choose a **Category** and write short **Notes** on how the sign is made.
   The notes show on the back of the card in the web app.
5. Pose the figure (see below).
6. Set **Cards folder** to this project's `cards` folder.
7. Click **Export Card PNG**.

Each export writes three things into the cards folder:

- `word-hello.png`: the card with its label
- `quiz/word-hello.png`: the same picture with no label, so practice mode doesn't give the answer away
- `signs/word-hello.json`: the card's "recipe" (every slider value, ghost and arrow)

It also adds or updates the card in `manifest.json`, which the web app reads.

To change a card later, click **Open Sign Recipe** and pick its file from `cards/signs/`.

## Posing

"Right" always means the **signer's** right, which appears on **your left** in the camera view.

**Right Hand / Left Hand panels**

- Pick a starting **Handshape** and click **Apply**, then fine-tune. There is a preset for every
  fingerspelled letter (P uses the K shape and Q the G shape, turned to point down), plus 5, 1,
  flat O, bent B and claw.
- Each finger has *Knuckle*, *Middle joint* and *Tip joint* bend sliders, plus *Side* to spread it.
- The thumb has *Across the front of the palm*, *Out to the side / toward pinky*, *Roll* and two
  joint bends. Its first bone sits inside the fleshy pad of the palm, as on a real hand, so the
  visible thumb starts about halfway up the palm.
- **Copy to left hand** copies the finger positions to the other hand.
- **Save** (at the bottom of the Right Hand panel) adds the current hand to your own handshape
  list, marked with ★ in the drop-down.

**Right Arm / Left Arm panels:** raise forward, raise sideways, rotate inward, elbow bend,
forearm turn (0 = palm facing in, about 90 = palm facing out), wrist bend and wrist side.

**Body & Face:** lean, turn, head nod/turn/tilt, and the face:

- **Eyebrows**: one slider raises (yes/no questions, topics) or furrows (wh-questions) both
  brows. Below it, each brow's inner end, middle and outer end move on their own, and
  **Squeeze** pulls the inner ends together. For example, inner ends up and outer ends down
  makes a worried face; one brow up makes a doubtful one.
- Mouth open, smile/frown, pucker (OO), puffed cheeks, and closing each eye.

A good fingerspelling position to start from: Right Arm *Raise sideways* 22, *Elbow bend* 150,
*Forearm turn* 90, *Wrist bend* -10, then **Framing & Look → Close-up on right hand**.

## Showing movement

### Start and End Position

Use the **Start & End Position** panel:

1. Pose the figure where the sign **starts**, then click **Set Start Position**.
   A faded copy stays there. **Start Position Opacity** sets how faded it is.
2. Move the figure to where the sign **ends**, then click **Set End Position**.
   - The faded start now shows only the arm(s) that actually moved.
   - If **Draw movement arrow automatically** is on, a curved arrow is drawn from the start
     hand position to the end. Clicking Set End Position again replaces that arrow.
   - If the hand only twists in place, no arrow is drawn; add an attached **Around** arrow instead.
3. To fix either position later, use **Sliders change: Start / End**, adjust, and click
   **Set End Position** again to refresh the arrow.

Tick **Start shows both arms** before Set Start Position for two-handed movements.
For movements with extra steps, **Add Middle Position** leaves another faded copy.

In **Figures & Ghosts** you can pick any figure to edit, change its **Opacity**, or use the
**Show** buttons to include or leave out the head, body, each arm and each hand.

### Arrows attached to a body part

These arrows follow the figure: change the pose and they move with it.

1. In **Movement Arrows → Attach an arrow to a body part**, choose the side (Right/Left),
   the **body part** (wrist, palm, forearm, elbow, shoulder, any fingertip, forehead, chin)
   and the type:
   - **Along**: a straight arrow pointing along the body part (for example, out from a fingertip).
   - **Around**: a circular arrow around the body part (for example, circling the wrist for a twist).
2. Click **Add Attached Arrow**. Its settings appear under **Selected**:
   - **Axis**: along the bone, toward the front/palm side, or sideways.
   - **Position**: nudge the arrow along the part's own directions.
   - **Size**: length for Along arrows; radius and how far around for Around arrows.
   - **Direction**: turn it around the axis, tilt it, or **Reverse** it.
   - **Always draw on top**: on keeps it in front of the body; off lets an Around arrow wrap
     behind the wrist in 3D.
   - **Detach** turns it into a normal arrow you can edit by hand.

The arrow attaches to the figure you're editing at the time, so you can attach one to the
start position as well.

### Free arrows

Click an arrow type in **Movement Arrows** (Straight, Arc, Back and forth, Circle, Zigzag).
It appears near the right hand.

- Move it: select it and press **G**, then click to drop it.
- Reshape it: press **Tab**, select a point, press **G** to move it, then **Tab** again.
- **Flip Direction** points the selected arrows the other way (attached arrows too).
- Arrow color and thickness apply to every arrow on the card.

Free arrows always draw on top of the figure.

## Proportions

The **Proportions** panel changes the figure's build. It applies to every figure on the card
and is saved with each card.

- **Neck length**.
- **Finger sections**: base, middle and tip length for every finger and the thumb, in millimeters.
  The thumb's base section is the part inside the palm.
- **Mirror left and right hands** (on by default): changing one hand changes the other.
  Turn it off to give each hand its own lengths; both hands' sliders then appear.
- **Thumb base position**: move where the thumb joins the hand (toward the fingers, toward the
  pinky, or out of the palm), in millimeters.
- **Shoulder position**: move each shoulder up, away from the body, or forward.
- The mirror setting applies to the thumb and shoulder positions too.
- **Reset Proportions** returns everything to the defaults.

Figures made with an older version of the add-on are rebuilt automatically when you open the
file (their poses are kept), so the length sliders work on them too. If the Card panel ever
shows "Figures from an older version", click **Update Figures**.

## Body shape and sculpting

The **Body Shape & Sculpting** panel changes the figure's body for every figure on the card:
**Head size**, **Body width**, **Body height**, **Body depth**, **Upper arm** and **Forearm**
length, **Arm thickness** and **Hand size**. **Reset Body Shape** returns to the defaults.

To reshape a part by hand:

1. Click the part on the figure (the nose, the head, the chest, a palm...).
2. Click **Sculpt Selected Part**. Blender switches to Sculpt Mode. Drag on the part to push it
   out; hold **Ctrl** to push in and **Shift** to smooth. **F** changes the brush size. The face
   and body are sculpted on both sides at once.
3. Click **Done Sculpting**. Every figure in the scene (including faded start positions) gets
   the new shape.

**Copy to Other Side** gives the matching part on the other side (the other hand, eye, ear,
cheek or eyebrow) a mirrored copy. **Reset Part** puts a sculpted part back.

Sculpted parts are kept when the figure is rebuilt by an add-on update. If you sculpt a finger
or arm section and later change its length, it is stretched to the new length.
Live tracking and recorded takes follow all of these changes (see the live tracking guide).

## Framing and look

**Fit Hands** and **Fit Hands + Face** (in the Card panel and in **Framing & Look**) zoom and
center the picture so every raised hand, every faded start position and every arrow is fully
visible above the label. Hands resting down by the hip are ignored. Use **Fit Hands + Face** for
signs made near the face or that need the facial expression (question words, for example).
Click it again after changing the pose.

**Framing & Look** also has fixed presets for the upper body, face plus hands, and a hand close-up.
The label is scaled with the camera, so the English term is the **same size on every card**
no matter how far you zoom. Label size is set in the Card panel.

Also here: **View angle** (turn the figure for a 3/4 view), skin and shirt colors, outlines,
transparent background, picture size and renderer (EEVEE is fast; Cycles is slower but softer).

## Live tracking and recording

The **Live Tracking & Recording** panel connects the figure to your webcam through the web
app's Record tab, so you can pose it by signing, and record signs as animated GIF cards.
See [live-tracking-guide.md](live-tracking-guide.md).

Recorded GIFs can have arrows of their own (paths that trace a movement, arrows attached to a
body part, and fixed shapes); they are set up in the web app's Review panel.

## Re-render every card from the command line

After changing the look or updating the add-on:

```bash
blender -b -P blender/asl_flashcard_editor.py -- --rebuild cards
```

This loads each recipe in `cards/signs/` and exports it again.

## How the figure is built (for learning the code)

- Every joint is an **Empty** object; body parts are meshes parented to the joint they move with.
- Each joint keeps its resting position in `matrix_parent_inverse`, so its `rotation_euler`
  is purely the pose. The sliders just set those rotations (see `apply_figure`).
- Every joint uses the same convention: local **-Z** points along the bone and local **-Y**
  is the direction it bends. The left side reuses the right side's code with X mirrored.
- Handshape presets are plain dictionaries near the top of the file (`HANDSHAPES`), so you can
  add more by copying one.
