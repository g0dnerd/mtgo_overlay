import cv2
import tkinter as tk
import tkinter.font as font
from data.resources import resource_path
import image_recognition.rec as rc
import crawler.fetch as fetch
from overlay.display import make_window_click_through
import capture.screen_capture as cap


def main():
    packs = []
    pack1 = [
        "Buried Alive",
        "Phyrexian Ironworks",
        "Territory Culler",
        "Twisted Riddlekeeper",
        "Wumpus Aberration",
        "Aerie Auxiliary",
        "Expanding Ooze",
        "Fanged Flames",
        "Nyxborn Hydra",
        "Riddle Gate Gargoyle",
        "Tune the Narrative",
    ]
    packs.append(pack1)
    pack2 = [
        "Herigast, Erupting Nullkite",
        "Drowner of Truth",
        "Phyrexian Ironworks",
        "Rush of Inspiration",
        "Solar Transformer",
        "Worn Powerstone",
        "Aether Spike",
        "Dog Umbra",
        "Refurbished Familiar",
        "Twisted Landscape",
        "Unfathomable Truths",
        "Utter Insignificance",
        "Voidpouncer",
        "Island",
    ]
    packs.append(pack2)
    pack3 = [
        "Estrid's Invocation",
        "Spymaster's Vault",
        "Golden-Tail Trainer",
        "Muster the Departed",
        "The Hunger Tide Rises",
        "Gift of the Viper",
        "Island",
    ]
    packs.append(pack3)
    # test_region("mh3", packs[2])
    # test_screengrab()
    test_labels('mh3', packs[2])


def test_screengrab():
    screengrab, screen = cap.capture_mtgo()
    width = screengrab.shape[1]
    height = screengrab.shape[0]
    screen_x = screen.topleft[0]
    screen_y = screen.topleft[1]
def test_labels(expansion, names):
    # screengrab, screen = cap.capture_mtgo()
    screengrab = cv2.imread(resource_path('draft_test_3.png'))
    root = tk.Tk()
    # root.withdraw()

    width = screengrab.shape[1]
    height = screengrab.shape[0]
    screen_x = 0
    screen_y = 0
    root.geometry(f"{width}x{height}+{screen_x}+{screen_y}")
    
    # Make the window borderless and transparent
    root.attributes("-alpha", 0)
    # root.wm_attributes("-topmost", 1)
    # root.overrideredirect(True)
    labels = []

    def create_label(card, rating):
        if card[0] in ['Plains', 'Island', 'Swamp', 'Mountain', 'Forest']:
            return
        label_window = tk.Toplevel(root)
        label_window.overrideredirect(True)
        label_window.wm_attributes("-topmost", True)
        label_window.configure(bg="#5b5b5b")
        if not rating:
            label_text = "GIH% N/A"
        else:
            label_text = f"GIH {rating}"

        label_font = font.Font(
            family="Segoe UI",
            size=11 if "Segoe UI" in font.families() else ("Arial", 11),
        )

        w_scale = 1920 / width
        h_scale = 1080 / height
        text_x = card[1][0] + card[1][2] - int(18 * w_scale)
        text_y = card[1][1] + int(7 * h_scale)

        text_width = label_font.measure(label_text)
        text_height = label_font.metrics("linespace")
        label_window.geometry(
            f"{text_width + 10}x{text_height + 10}+{text_x - text_width}+{text_y}"
        )

        label = tk.Label(
            label_window, text=label_text, font=label_font, fg="white", bg="#707070"
        )

        label.pack(fill=tk.BOTH, expand=True)
        hwnd = label_window.winfo_id()
        make_window_click_through(hwnd)
        labels.append(label_window)

    def fetch_cards():
        cards = rc.get_pos_and_names(expansion, screengrab, names)

        cards = rc.normalize_positions(cards)

        ratings = fetch.get_card_ratings(expansion, [card for card in cards.keys()])
        for card, rating in zip(cards.items(), ratings):
            root.after(0, create_label, card, rating)

    make_window_click_through(root.winfo_id())
    root.after(0, fetch_cards)
    root.mainloop()


def test_region(expansion: str, names: list):
    screen = cv2.imread(resource_path("draft_test_3.png"))
    cards = rc.get_pos_and_names(expansion, screen, names)
    for name, pos in cards:
        x, y, w, h = pos
        # Draw the bounding box
        cv2.rectangle(screen, (x, y), (x + w, y + h), (0, 255, 0), 2)
        # Put the card name
        cv2.putText(
            screen,
            name,
            (x, y + 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

    # Display the result
    cv2.imshow("Detected Cards", screen)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
