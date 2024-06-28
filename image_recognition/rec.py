import logging
import sys
import numpy as np
import cv2
import crawler.fetch
from data.resources import resource_path
import image_recognition.preprocessing as pre

def initialize_sift():
    # Create SIFT object
    sift = cv2.SIFT_create()
    return sift

def detect_and_compute_features(image, sift):
    # Convert image to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Detect SIFT features and compute descriptors
    kp, des = sift.detectAndCompute(gray, None)
    return kp, des

def match_features(des1, des2):
    # Create FLANN matcher
    idx_params = dict(algorithm=1, trees=5)
    search_params = dict(checks=50)
    flann = cv2.FlannBasedMatcher(idx_params, search_params)

    # Match descriptors
    matches = flann.knnMatch(des1, des2, k=2)

    # Store all the good matches as per Lowe's ratio test.
    good_matches = []
    for m, n in matches:
        if m.distance < 0.7 * n.distance:
            good_matches.append(m)
    return good_matches
    
def find_homography_draw_box(kp1, kp2, matches, card_shape):

    if len(matches) > 65:  # Define a minimum match count
        points1 = np.zeros((len(matches), 2), dtype=np.float32)
        points2 = np.zeros((len(matches), 2), dtype=np.float32)

        for i, match in enumerate(matches):
            points1[i, :] = kp1[match.queryIdx].pt
            points2[i, :] = kp2[match.trainIdx].pt

        # Find homography
        H, mask = cv2.findHomography(points1, points2, cv2.RANSAC, 5.0)
        matchesMask = mask.ravel().tolist()

        # Check if the found homography is good
        inliers_count = np.sum(matchesMask)  # Number of inliers
        total_matches = len(matchesMask)  # Total matches
        confidence = inliers_count / total_matches  # Confidence as a percentage

        if confidence > 0.59:  # Set a confidence threshold
            # Perspective transformation and draw box
            height, width = card_shape[:2]
            points = np.float32([[0, 0], [0, height-1], [width-1, height-1], [width-1, 0]]).reshape(-1, 1, 2)
            transformed_points = cv2.perspectiveTransform(points, H)
        else:
            transformed_points = None
    else:
        matchesMask = None
        transformed_points = None

    return transformed_points, confidence if 'confidence' in locals() else 0

def prepare_card_images(names, scale_factor, sift):
    card_images = {}    
    for name in names:
        image = crawler.fetch.prepare_card_image(name=name, save=True)
        image = pre.resize_image(image, scale_factor)
        kp, des = detect_and_compute_features(image, sift)
        card_images[name] = (kp, des, image.shape)
    return card_images

def get_pos_and_names(screen, names: list):
    log_path = resource_path('debug.log')
    logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                            handlers=[
                                logging.FileHandler(log_path),
                                logging.StreamHandler(sys.stdout)
                                ])
    scale_factor = 80
    sift = initialize_sift()
    boxes = []
    cards_found = []
    found_names = set()
    card_region = (0, 0, screen.shape[1], int(screen.shape[0]//1.7))
    if not card_region:
        raise ValueError('Not card region detected.')
    screen_shot, (offset_x, offset_y) = pre.crop_image_to_region(screen, card_region)

    card_images = prepare_card_images(names, scale_factor, sift)

    kp2, des2 = detect_and_compute_features(screen_shot, sift)
    for name, (kp1, des1, shape) in card_images.items():
        if name in found_names:
            continue
        try:
            matches = match_features(des1, des2)
        except:
            raise ValueError(f'Feature matching failed for card {name} failed.')
        logging.info(f'Found {len(matches)} matches for card {name}')
        pts, confidence = find_homography_draw_box(kp1, kp2, matches, shape)

        if confidence > 0.59:
            logging.info(f'Found card {name} on screen with a confidence of {confidence}!')
            pts = (int(pts[0][0][0] + 100), int(pts[0][0][1] + 30))
            adjusted_pts = (pts[0] + offset_x, pts[1] + offset_y)
            boxes.append(adjusted_pts)
            cards_found.append(name)
            found_names.add(name)
        else:
            logging.info(f'Could not find card {name} on screen (confidence {confidence})!')

    return boxes, cards_found