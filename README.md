# MTGO Draft Helper

A click-through, always-on-top overlay that draws 17Lands **Game-in-Hand Win Rate (GIH WR)** next to each card in the current Magic: The Gathering Online draft pack.
Card names come from MTGO's draft log, card positions from OpenCV template matching, and ratings from 17Lands.

The overlay is transparent to clicks, so MTGO stays fully usable underneath it.

## Install

Download `MtgoOverlaySetup.exe` from the [latest release](https://github.com/g0dnerd/mtgo_overlay/releases) and run it.
It installs per-user (no admin prompt) into `%LOCALAPPDATA%\Programs\MtgoOverlay`, adds a Start-menu shortcut, and can optionally start on sign-in.

Because the installer is **unsigned**, Windows SmartScreen shows a warning the first time you run it - click **More info -> Run anyway**.

## Setup

On first launch a short wizard walks you through everything:

1. **Accept the privacy notice** (see [Privacy](#privacy) below).
2. **Pick your MTGO log folder** - usually `%APPDATA%\Wizards of the Coast\Magic Online`.
3. **Enter your exact MTGO screen name** (pre-filled from existing logs when possible).
4. **Choose a win-rate source** - live 17Lands or your own CSV export.
5. **Enable draft logging in MTGO** - Options -> Game History -> turn on draft logging. The overlay can only see your picks once MTGO is writing a log.

Start (or replay) a draft and labels appear over the pack, updating each pick.
If win rates don't show up, use the tray menu's **Setup status…** to see what's missing.

## Configuration

Settings live in `%APPDATA%\MtgoOverlay\config.toml`. The wizard and tray menu write the common ones; you can also edit the file directly.
Caches and logs live under `%LOCALAPPDATA%\MtgoOverlay\`.

| Key                   | Default          | Meaning                                                                                    |
| --------------------- | ---------------- | ------------------------------------------------------------------------------------------ |
| `mtgo_username`       | `""`             | Your exact MTGO screen name (matched against the log).                                     |
| `log_dir`             | `""`             | Folder MTGO writes draft logs to.                                                          |
| `fmt`                 | `"PremierDraft"` | 17Lands format: `PremierDraft`, `TradDraft`, `QuickDraft`, `Sealed`, `TradSealed`.         |
| `use_live_17lands`    | `false`          | Fetch ratings live from 17Lands instead of the CSV (see [Ratings](#ratings-data-17lands)). |
| `manual_csv_path`     | `""`             | Path to a `card_ratings.csv` exported from 17Lands.                                        |
| `user_group`          | `"top"`          | Which cohort the win rate reflects: `"top"` players or `"all"`.                            |
| `user_agent`          | see file         | Identifying User-Agent sent with the live 17Lands request.                                 |
| `accepted_disclaimer` | `false`          | Set once the first-run privacy notice is accepted.                                         |

Overlay appearance is under an `[overlay]` table. Every size/offset is a _fraction of the card box_, so labels scale with any MTGO window size and DPI:

| Key                | Default      | Meaning                                                    |
| ------------------ | ------------ | ---------------------------------------------------------- |
| `font_family`      | `"Segoe UI"` | Label font.                                                |
| `fg`               | `"#ffffff"`  | Text color.                                                |
| `unknown_color`    | `"#6b7280"`  | Pill color for cards with no rating.                       |
| `font_h_frac`      | `0.072`      | Number height, as a fraction of card height.               |
| `pill_bottom_frac` | `0.23`       | Pill's bottom edge, as a fraction of card height.          |
| `inset_x_frac`     | `0.045`      | Pill inset from the card's right edge (fraction of width). |
| `pad_x_frac`       | `0.035`      | Horizontal text padding (fraction of card width).          |
| `pad_y_frac`       | `0.012`      | Vertical text padding (fraction of card height).           |

## Ratings data (17Lands)

By default the overlay reads a `card_ratings.csv` you download yourself from 17Lands' card data page - point `manual_csv_path` at it.
Setting `use_live_17lands = true` instead pulls current win rates from 17Lands' internal endpoint (one small, cached request per set/format per 24h).
That endpoint is undocumented, so it is **off by default** - review 17Lands' usage guidelines before enabling it.
To respect those guidelines, live win rates for a just-released set are held back for 12 days; during that window supply your own CSV to see ratings for the new set.

## Privacy

- **Your screen stays on your machine.** The overlay screenshots the MTGO window to locate cards and processes those screenshots **locally**. They are never uploaded anywhere.
- **The only outbound requests** are to **Scryfall** (card images) and **17Lands** (win-rate data). No personal data is sent.

## Credits & attribution

- **Win rate data** comes from [**17Lands**](https://www.17lands.com/). This project is an independent tool and is **not affiliated with, endorsed by, or sponsored by** 17Lands. Please support 17Lands and review their usage guidelines before enabling the live fetch.
- **Card artwork** used for recognition comes from the
  [**Scryfall**](https://scryfall.com/) API, per their
  [API guidelines](https://scryfall.com/docs/api). Card images and Oracle text are © Wizards of the Coast.
- _Magic: The Gathering_ and _Magic Online_ are trademarks of **Wizards of the Coast LLC**. Per the [Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy):

  > MTGO Draft Helper is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

## License

Released under the **GNU General Public License v3.0 or later**. See [`LICENSE`](LICENSE) for the full text.
