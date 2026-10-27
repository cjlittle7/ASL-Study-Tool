# ASL Study Tool

A web app for learning American Sign Language with illustrated flashcards, short grammar
lessons and self-checked practice, plus a Blender add-on for drawing the flashcards.

**Status:** early prototype. The sample cards are drafts drawn with the editor and should be
checked by a fluent signer before anyone relies on them.

## What's here

| Folder | What it is |
| --- | --- |
| `index.html`, `css/`, `js/` | The web app (plain HTML, CSS and JavaScript, no build step) |
| `data/` | Lessons, the grammar dictionary and practice sentences, as JSON you can edit |
| `cards/` | Exported flashcards. `manifest.json` lists them; `quiz/` has copies without the English label; `signs/` has the "recipe" for each card so it can be re-rendered |
| `blender/asl_flashcard_editor.py` | The Blender add-on that makes the cards |
| `docs/blender-guide.md` | Step-by-step guide to the editor |

## Web app features

- **Learn:** lessons on how signs are built, the alphabet and ASL sentence order. Highlighted
  grammar words (noun, adverb, article...) open the dictionary in the bottom-right corner.
- **Cards:** browse every exported card with notes on how to make the sign.
- **Practice:** self-checked flashcards, sign to English or English to sign. Cards you miss
  come back first.
- **Sentences:** translate a row of sign cards into English, or an English sentence into signs.
- **Progress:** answers per day, percent correct over time, and a success rate for every sign.
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

See [docs/blender-guide.md](docs/blender-guide.md). In short: install the add-on in Blender,
click **New Card Scene**, pose the figure, type the English term, set the cards folder to this
project's `cards/` folder and click **Export Card PNG**. Reload the web app and the card is there.

To re-render every card after changing the look (colors, outline width, size):

```bash
blender -b -P blender/asl_flashcard_editor.py -- --rebuild cards
```

## Roadmap

- Online profiles that sync between devices (Supabase free tier works with GitHub Pages)
- More cards: the rest of the alphabet, numbers and about 100 common words
- Search for outside tutorials (YouTube and free ASL sites)
- Camera practice: recognize fingerspelling from the webcam, then whole signs
