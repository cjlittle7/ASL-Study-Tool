# ASL Study Tool

A web app for learning American Sign Language with illustrated flashcards, short grammar
lessons and self-checked practice, plus a Blender add-on for drawing the flashcards and for
recording signs with live webcam tracking.

**Status:** early prototype. The cards (all 26 letters, the six question words, 5 and HELLO) are
drafts drawn with the editor and should be checked by a fluent signer before anyone relies on them.

## What's here

| Folder | What it is |
| --- | --- |
| `index.html`, `css/`, `js/` | The web app (plain HTML, CSS and JavaScript, no build step) |
| `data/` | Lessons, the grammar dictionary and practice sentences, as JSON you can edit |
| `data/lifeprint-practice.json` | 300 practice sentences from the Lifeprint practice sheets (lessons 1-15), with English and the cards each one uses |
| `data/card-plan.json` | Every card the course needs; the Cards tab lists the ones not drawn yet |
| `cards/` | Exported flashcards. `manifest.json` lists them; `quiz/` has copies without the English label; `signs/` has the "recipe" for each card so it can be re-rendered |
| `blender/asl_flashcard_editor.py` | The Blender add-on that makes the cards |
| `docs/blender-guide.md` | Step-by-step guide to the editor |
| `docs/live-tracking-guide.md` | Live webcam tracking and recording signs as animated GIFs |
| `js/record/` | The Record tab: webcam tracking (MediaPipe) and the link to Blender |

## Web app features

- **Learn:** lessons on how signs are built, the alphabet and ASL sentence order. Highlighted
  grammar words (noun, adverb, article...) open the dictionary in the bottom-right corner.
- **Cards:** browse every exported card with notes on how to make the sign, plus a checklist of the
  cards still to draw, ordered by lesson and by how many sentences use each sign.
- **Practice:** self-checked flashcards, sign to English or English to sign. Cards you miss
  come back first.
- **Sentences:** practice sets for the starter sentences and each Lifeprint lesson (1-15). Three
  directions: signs to English, gloss to English (works before cards are drawn), and English to signs.
- **Progress:** answers per day, percent correct over time, and a success rate for every sign.
- **Record:** your webcam moves the Blender figure in real time (fingers, arms, head, eyebrows
  and facial expression). Hands are placed by position, so touching your nose touches the
  figure's nose. Add your phone as a second camera at any angle for measured (not estimated)
  depth. Record a sign, trim it, describe it, add arrows (movement paths, arrows on a
  body part, fixed shapes) with controls for each one, retry with one key, and save it as an
  animated GIF card. See [docs/live-tracking-guide.md](docs/live-tracking-guide.md).
- **Profiles:** a nickname plus a recovery code. Saved in the browser for now; online sync
  between devices is the next planned step.

## Run it on your computer

Browsers won't load the data files from a page opened by double-clicking `index.html`,
so start a tiny local web server from this folder:

```bash
python3 -m http.server 8000
```

Then open <http://localhost:8000>. (In VS Code, the "Live Server" extension does the same.)

## Publish with GitHub Pages

1. Push this folder to `https://github.com/cjlittle7/ASL-Study-Tool`.
2. On GitHub: **Settings → Pages → Build and deployment → Source: Deploy from a branch**,
   branch `main`, folder `/ (root)`.
3. After a minute the site is live at `https://cjlittle7.github.io/ASL-Study-Tool/`.

## Making cards

The figure's body can be reshaped with sliders (head size, body width, arm lengths, hand
size...) or sculpted with Blender's brushes; live tracking and recordings follow the new shape.

See [docs/blender-guide.md](docs/blender-guide.md). In short: install the add-on in Blender,
click **New Card Scene**, pose the figure, type the English term, set the cards folder to this
project's `cards/` folder and click **Export Card PNG**. Reload the web app and the card is there.

To re-render every card after changing the look (colors, outline width, size):

```bash
blender -b -P blender/asl_flashcard_editor.py -- --rebuild cards
```

## Credits and permissions

The practice sentences in `data/lifeprint-practice.json` come from Dr. Bill Vicars' practice sheets on
[Lifeprint.com](https://www.lifeprint.com/) (ASL University). About a third had no English on the
sheet; those translations were written for this project and are marked `englishAdded`. Lifeprint's
material is copyrighted, so check its terms of use, or ask Dr. Vicars, before publishing this file in
a public repository. The sign illustrations are this project's own.

## Roadmap

- Online profiles that sync between devices (Supabase free tier works with GitHub Pages)
- Word cards for the Lifeprint practice sentences (the Cards tab lists them in order)
- Search for outside tutorials (YouTube and free ASL sites)
- Camera practice: use the Record tab's tracking to check your fingerspelling, then whole signs
