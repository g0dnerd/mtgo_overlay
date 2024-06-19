"""Test"""

from capture.screen_capture import capture_mtgo, get_mtgo_window
from image_recognition.fetch import prepare_card_image, update_bulk_data
from image_recognition.rec import find_card_on_screen

def main():
    # update_bulk_data()
    card_img = prepare_card_image(name='Faithful Watchdog')
    mtgo_window = get_mtgo_window()
    mtgo_capture = capture_mtgo(mtgo_window)
    print(find_card_on_screen(card_img, mtgo_capture))
    
if __name__ == '__main__':
    main()
