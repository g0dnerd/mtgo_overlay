"""Locates card images on a screen region."""

import numpy as np
import time
import cv2
import json
import data.resources as util
import image_recognition.preprocessing as pre
from crawler.fetch import get_card_image
import concurrent.futures

MIN_MATCH_COUNT = 50
MIN_CONFIDENCE = 0.55


def initialize_sift():
    """Returns a SIFT instance. Pretty performance intensive."""
    sift = cv2.SIFT_create()
    return sift


def detect_and_compute_features(image, sift):
    """Detects keypoints and descriptors for the 'image' mat-like."""
    # Convert image to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Detect SIFT features and compute descriptors
    kp, des = sift.detectAndCompute(gray, None)
    return kp, des


def match_features(des1, des2):
    """Uses a FLANN-based matcher to find matches between descriptors 1 and 2.
    Uses Lowe's ratio test with a threshold of .7
    """
    # Create FLANN matcher
    idx_params = {"algorithm": 1, "trees": 5}
    search_params = {"checks": 50}
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
    """Using keypoints 1 and 2 and the matches between those images' descriptors,
    attempts to find the homography draw box for the found image and a confidence value.
    """
    if len(matches) > MIN_MATCH_COUNT:  # Define a minimum match count
        points1 = np.zeros((len(matches), 2), dtype=np.float32)
        points2 = np.zeros((len(matches), 2), dtype=np.float32)

        for i, match in enumerate(matches):
            points1[i, :] = kp1[match.queryIdx].pt
            points2[i, :] = kp2[match.trainIdx].pt

        # Find homography
        h, mask = cv2.findHomography(points1, points2, cv2.RANSAC, 5.0)
        matches_mask = mask.ravel().tolist()

        transformed_points, confidence = get_confidence(matches_mask, h, card_shape)

    else:
        matches_mask = None
        transformed_points = None
        confidence = 0

    return transformed_points, confidence


def prepare_card_images(expansion, names, scale_factor, sift):
    """For the given list of card names, extracts keypoints, descriptors and shape."""

    def process_image(name, exp, id_string):
        image = get_card_image(expansion, exp, id_string=id_string)
        image = pre.resize_image(image, scale_factor)
        kp, des = detect_and_compute_features(image, sift)
        return (kp, des)

    card_images = {}

    # Load the JSON data once
    with open(
        util.resource_path(f"{expansion}/card_variants.json"), "r", encoding="utf-8"
    ) as f:
        variant_data = json.load(f)

    # Prepare tasks for concurrent execution
    tasks = []
    for name in names:
        for exp, ids in variant_data.get(name, {}).items():
            for id_string in ids:
                tasks.append((name, exp, id_string))

    # Process images concurrently
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(process_image, name, exp, id_string): (name, exp, id_string)
            for name, exp, id_string in tasks
        }

        for future in concurrent.futures.as_completed(futures):
            name, exp, id_string = futures[future]
            kp, des = future.result()
            if name not in card_images:
                card_images[name] = [(kp, des)]
            else:
                card_images[name].append((kp, des))

    return card_images


def get_confidence(matches, kp1, kp2):
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
    confidence = inliers_count / total_matches
    return confidence


def process_roi(roi_data, card_images, sift):
    coords, roi = roi_data

    kp2, des2 = detect_and_compute_features(roi, sift)

    for name, details in card_images.items():
        for kp1, des1 in details:
            try:
                matches = match_features(des1, des2)
            except Exception as e:
                print(f"Feature matching failed for card {name} failed.")
                raise ValueError(
                    f"Feature matching failed for card {name} failed."
                ) from e

            if len(matches) >= 58:
                confidence = get_confidence(matches, kp1, kp2)
                if confidence > 0.55:
                    util.log_info(
                        f"Found {name} with {len(matches)} matches, confidence {confidence}"
                    )
                    return [(name, coords)]
                else:
                    util.log_info(
                        f'Confidence threshold not reached: {name} - {len(matches)} - {confidence}'
                    )
            elif len(matches) >= 30:
                util.log_info(
                    f'Match amount threshold not reached: {name} - {len(matches)}'
                )

    return []


def get_pos_and_names(expansion, screen, names: list):
    """Parses through the given pack and tries to match every card in the screenshot."""
    sift = initialize_sift()

    scale_factor = 80
    cards_found = {}

    # Try to crop off the window edges and booster picture
    height = screen.shape[0]
    vertical_cutoff = int(height // 13.5)
    screen = screen[vertical_cutoff:]

    now = time.time()
    regions = pre.detect_card_region(screen, scale_factor) #, show=True)
    new = time.time()
    elapsed = new - now
    util.log_info(f"Detecting card regions took {elapsed:.4f} seconds")
    count = 0
    while not regions:
        regions = pre.detect_card_region(screen, scale_factor)
        count += 1
        if regions or count == 5:
            break

    if not regions:
        raise ValueError("Could not find card regions!")

    rois = [
        (
            region,
            screen[
                region[1] : region[1] + region[3], region[0] : region[0] + region[2]
            ],
        )
        for region in regions
    ]

    now = time.time()
    card_images = prepare_card_images(expansion, names, scale_factor, sift)
    new = time.time()
    elapsed = new - now
    util.log_info(f"Preparing card images took {elapsed:.4f} seconds.")

    now = time.time()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_roi = {
            executor.submit(process_roi, roi, card_images, sift): roi for roi in rois
        }
        for future in concurrent.futures.as_completed(future_to_roi):
            try:
                found_cards = future.result()
                for name, coords in found_cards:
                    x, y, w, h = coords
                    y += vertical_cutoff
                    cards_found.update({name: (x, y, w, h)})
            except Exception as exc:
                util.log_info(f"Generated an exception: {exc}")

    new = time.time()
    elapsed = new - now
    util.log_info(f"Finding all cards took {elapsed:.4f} seconds.")
    return cards_found


def normalize_positions(cards):
    return cards
    # Extract y-values
    y_values = np.array([coord[1] for coord in cards.values()])

    # Check if all y-values are within 20% of the range of y-values
    y_range = np.ptp(y_values)  # Peak-to-peak (ma<cacamox-min) range
    y_mean = np.mean(y_values)
    threshold = 0.2 * y_mean

    print(threshold, y_range)

    if y_range <= threshold:
        # If the range is within 20%, normalize to the mean y-value
        normalized_y = int(y_mean)
        normalized = {name: (x, normalized_y, w, h) for name, (x, y, w, h) in cards.items()}
    else:
        # If not, proceed with clustering
        from sklearn.cluster import KMeans
        y_values = y_values.reshape(-1, 1)
        kmeans = KMeans(n_clusters=2, random_state=0).fit(y_values)
        labels = kmeans.labels_
        centers = kmeans.cluster_centers_.flatten()

        # normalized_coordinates = []
        for i, (card, (x, y, w, h)) in enumerate(cards.items()):
            # Find the cluster center corresponding to this y-value
            new_y = centers[labels[i]]
            normalized = {name: (x, int(new_y), w, h) for name, (x, y, w, h) in cards.items()}
            # normalized_coordinates.append((card, (x, int(new_y), w, h)))

    return normalized