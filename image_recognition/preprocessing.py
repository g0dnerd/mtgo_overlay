"""Holds various utility functions for image preprocessing."""

import cv2

# Constants for detecting cards on screen
MIN_DIM = 30000
MAX_DIM = 60000


def enhance_contrast(image):
    """Uses CLAHE to enhance the image contrast."""
    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return enhanced


def apply_morphology(image):
    """Applies morphological operations to fill gaps in contours."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(image, cv2.MORPH_CLOSE, kernel)
    return closed


def apply_threshold(image):
    """Applies basic thresholding
    (if a pixel has a value below the threshold, it's 0, 1 otherwise).
    """
    # Assuming the image is already converted to grayscale
    thresh = cv2.adaptiveThreshold(
        image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    return thresh


def resize_image(image, scale_percent):
    """Scales down the image by the provided scale percent
    :param scale_percent (int):
    """
    width = int(image.shape[1] * scale_percent / 100)
    height = int(image.shape[0] * scale_percent / 100)
    dim = (width, height)
    resized = cv2.resize(image, dim, interpolation=cv2.INTER_AREA)
    return resized


def detect_card_region(image, scale_factor, show=False):
    """Tries to find the region on screen where the cards are."""
    if show:
        contour_image = image.copy()

    height = image.shape[0]
    width = image.shape[1]
    vertical_cutoff = height - int(height // 2.5)
    image = image[:vertical_cutoff]

    image = resize_image(image, scale_factor)
    
    height = image.shape[0]
    width = image.shape[1]

    enhanced = enhance_contrast(image)

    # Assumes image contains the top half of the screen.
    blurred = cv2.GaussianBlur(enhanced, (7, 7), 0)
    image = cv2.Canny(blurred, 50, 250)

    image = apply_morphology(image)

    # Find contours
    contours, _ = cv2.findContours(image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    dim_scale = ((width*height)/(1920*1080))
    adjusted_min_dim = MIN_DIM * dim_scale 
    adjusted_max_dim = MAX_DIM * dim_scale
    print(f"Adjusted min dim: {adjusted_min_dim}, adjusted max dim: {adjusted_max_dim}")

    card_contours = []
    # Filter contours based on aspect ratio and size to detect cards
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        x = int(x / scale_factor * 100)
        y = int(y / scale_factor * 100)
        w = int(w / scale_factor * 100)
        h = int(h / scale_factor * 100)
        aspect_ratio = w / float(h)
        if 0.5 < aspect_ratio < 1.5:
            if adjusted_min_dim < (w * h * dim_scale) < adjusted_max_dim:
                bbox = (x, y, w, h)
                card_contours.append(bbox)
                if show:
                    info_text = f"D: {w* h} AR:{aspect_ratio:.2f}"
                    cv2.rectangle(contour_image, (x, y), (x+w,y+h), (0, 255, 0), 3)
                    cv2.putText(contour_image, info_text, (x, y + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            else:
                print(f"Skipping contour: {(w * h * dim_scale)}, {aspect_ratio:.2f}")

    if show:
        cv2.imshow('Contours', contour_image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    return card_contours


def crop_image_to_region(image, region):
    """Crops an image to the specified x/y + w/h"""
    x, y, w, h = region
    return image[y : y + h, x : x + w], (x, y)
