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

- Pick a starting **Handshape** (A, B, C, 5, L, Y...) and click **Apply**, then fine-tune.
- Each finger has *Knuckle*, *Middle joint* and *Tip joint* bend sliders, plus *Side* to spread it.
- The thumb has *Across palm*, *Away from palm*, *Roll* and two joint bends.
- **Copy to left hand** copies the finger positions to the other hand.
- **Save** (at the bottom of the Right Hand panel) adds the current hand to your own handshape
  list, marked with ★ in the drop-down.

**Right Arm / Left Arm panels:** raise forward, raise sideways, rotate inward, elbow bend,
forearm turn (0 = palm facing in, about 90 = palm facing out), wrist bend and wrist side.

**Body & Face:** lean, turn, head nod/turn/tilt, and **Eyebrows** (raised for yes/no
questions and topics, furrowed for wh-questions).

A good fingerspelling position to start from: Right Arm *Raise sideways* 22, *Elbow bend* 150,
*Forearm turn* 90, *Wrist bend* -10, then **Framing & Look → Close-up on right hand**.

## Showing movement

### Ghosts (where the movement starts)

1. Pose the figure in the **starting** position.
2. Click **Snapshot as Ghost**. A faded copy stays behind.
3. Now move the solid figure to the **ending** position.

You can add several ghosts for multi-step movements. In **Figures & Ghosts**, click a figure's
name to edit it, change its **Opacity**, or use the **Show** buttons to include or leave out
the head, body, each arm and each hand. New ghosts show only the right arm; tick
**Ghost both arms** first for two-handed movements.

### Arrows

Click an arrow type in **Movement Arrows** (Straight, Arc, Back and forth, Circle, Zigzag).
It appears near the right hand.

- Move it: select it and press **G**, then click to drop it.
- Reshape it: press **Tab**, select a point, press **G** to move it, then **Tab** again.
- **Flip Direction** points it the other way.
- Arrow color and thickness apply to every arrow on the card.

Arrows always draw on top of the figure.

## Framing and look

**Framing & Look** has presets for the upper body, face plus hands, and a hand close-up.
The label is scaled with the camera, so the English term is the **same size on every card**
no matter how far you zoom. Label size is set in the Card panel.

Also here: **View angle** (turn the figure for a 3/4 view), skin and shirt colors, outlines,
transparent background, picture size and renderer (EEVEE is fast; Cycles is slower but softer).

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
