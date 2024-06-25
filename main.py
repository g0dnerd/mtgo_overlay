"""Test"""

import os
import cv2
import crawler.fetch
import crawler.card_names
import overlay.display as dp
from image_recognition.preprocessing import crop_image_to_region
from capture.screen_capture import capture_mtgo

def main():
    if not os.path.isfile('data/bulk_data.json'):
        crawler.fetch.update_bulk_data()
        
    dp.RatingOverlay()

if __name__ == '__main__':
    main()
