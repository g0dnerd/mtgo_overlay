import numpy as np
import cv2

def find_card_on_screen(card_image, screen_shot):
    screen_img = cv2.imread(screen_shot, 0)
    card_img = cv2.cvtColor(card_image, cv2.COLOR_BGR2GRAY)
    w, h = card_img.shape[::-1]

    # Apply template Matching
    res = cv2.matchTemplate(screen_img, card_img, cv2.TM_CCOEFF_NORMED)
    threshold = 0.8
    loc = np.where(res >= threshold)
    for pt in zip(*loc[::-1]): # Switch x and y
        cv2.rectangle(screen_img, pt, (pt[0] + w, pt[1] + h), (0, 0, 255), 2)

    cv2.imwrite('result.png', screen_img)
    cv2.imwrite('cv2card.png', card_img)
    print(loc)
    return loc