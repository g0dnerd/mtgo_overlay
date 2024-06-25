import os
import json
import pytesseract
import cv2
import crawler.card_names
import crawler.fetch

def crop_to_title(img):
    height, width, _ = img.shape

    bottom_slice_height = int(height * 0.05)
    top_slice_height = int(height * 0.1)
    left_slice_width = int(width * 0.08)
    right_slice_width = int(width * 0.75)

    cropped_image = img[bottom_slice_height:top_slice_height, left_slice_width:right_slice_width]
    return cropped_image

def text_from_img(path):
    print(f'Extracting from {path}')
    img = cv2.imread(path)
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    print(f'Image has size {img.size}')
    print(pytesseract.image_to_string(img_rgb))

def all_titles_for_set(expansion_code):
    if not os.path.exists(f'data/{expansion_code}'):
        crawler.card_names.get_cards_for_set(expansion_code)
        os.mkdir(f'data/{expansion_code}/images')
    
    with open(f'data/{expansion_code}/cards.json') as json_data:
        cards = json.load(json_data)
        json_data.close()
    
    for idx, card in enumerate(cards):
        id = card['id']
        fname = card['name'].replace(' ', '')
        print(f'{idx + 1}/{len(cards)}: Looking at card ID {id} and name {card['name']}')
        if os.path.exists(f'data/{expansion_code}/images/{fname}.png'):
            print(f'Card already cached.')
            continue
        img = crawler.fetch.prepare_card_image(name=card['name'])
        cropped = crop_to_title(img)
        cv2.imwrite(f'data/{expansion_code}/images/{fname}.png', cropped)