#!/usr/bin/env python3
#
# Not copyrighted at all by Johan Kanflo in 2022 - CC0 applies
#
# Pull data from the inofficial Trafikverket road condition map tile API.
# Analyze each tile and post its color (hex and name) to an MQTT topic:
#  home/roadcondition/102/color 00ff00
#  home/roadcondition/102/condition green

import sys
import io
import time
import tempfile
try:
    import requests
except ImportError:
    print("sudo -H python -m pip install requests")
    sys.exit(1)
import logging
import argparse
import configparser
from subprocess import Popen, PIPE
try:
    from PIL import Image
    from PIL.PngImagePlugin import PngImageFile
except ImportError:
    print("sudo -H python -m pip install Pillow'")
    sys.exit(1)
try:
    import mqttwrapper
except ImportError:
    print("sudo -H python -m pip install git+https://github.com/kanflo/mqttwrapper")
    sys.exit(1)
import socket

def get_image(url: str) -> PngImageFile:
    """Fetch image and load into PIL

    Args:
        url (str): Image URL

    Returns:
        PngImageFile: PIL image
    """
    headers = {}
    headers['Accept'] = 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
    headers['Accept-Language'] = 'en-US,en;q=0.9,sv;q=0.8'
    headers['Connection'] = 'keep-alive'
    headers['Referer'] = 'https://www.trafikverket.se/'
    headers['Sec-Fetch-Dest'] = 'image'
    headers['Sec-Fetch-Mode'] = 'no-cors'
    headers['Sec-Fetch-Site'] = 'same-site'
    headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36'
    headers['sec-ch-ua'] = '" Not A;Brand";v="99", "Chromium";v="102", "Google Chrome";v="102"'
    headers['sec-ch-ua-mobile'] = '?0'
    headers['sec-ch-ua-platform'] = '"Linux"'

    im = None
    r = requests.get(url, headers = headers, stream=True)
    if r.status_code == 200:
        buffer = tempfile.SpooledTemporaryFile(max_size=1e9)
        downloaded = 0
        for chunk in r.iter_content(chunk_size=1024):
            downloaded += len(chunk)
            buffer.write(chunk)
        buffer.seek(0)
        im = Image.open(io.BytesIO(buffer.read()))
        buffer.close()
    else:
        logging.error("Error: API access failed with %d" % resp.status_code)
    return im


def get_prominent_color(im: PngImageFile) -> tuple:
    """Get the prominent color from image in URL

    Args:
        url (str): _description_

    Returns:
        _type_: _description_
    """
    histogram = {}
    limit = 10

    try:
        for i in range(im.size[0]):
            for j in range(im.size[1]):
                px = im.getpixel((i,j))
                if px != (0, 0, 0) and px != (0, 0, 0, 0) and px != (255, 255, 255):
                    if abs(px[0]-px[1]) > limit or abs(px[0]-px[2]) > limit or abs(px[1]-px[2]) > limit:
                        if True:
                            if not px in histogram:
                                histogram[px] = 1
                            else:
                                histogram[px] += 1
                        else:
                            key = "%02x%02x%02x%02x" % (px[0], px[1], px[2], px[3])
                            if not key in histogram:
                                histogram[key] = 1
                            else:
                                histogram[key] += 1
    except AttributeError as e:
        pass # Grayscale image?

    px_max = (0, 0, 0)
    max_count = 0
    for px in histogram:
        if histogram[px] > max_count:
            px_max = px
            max_count = histogram[px]

    return (((px_max[0], px_max[1], px_max[2])))


def mqtt_callback(topic: str, payload: str):
    pass


def color_saturate(color: tuple) -> tuple:
    """Saturate color (eg. #cc1000 -> #ff0000)

    Args:
        color (tuple[int, int, int]): RGB color tupe

    Returns:
        tuple[int, int, int]: Saturated RGB color tuple
    """
    threshold = 70
    r = 255 if color[0] > threshold else 0
    g = 255 if color[1] > threshold else 0
    b = 255 if color[2] > threshold else 0
    return (r, g, b)


def name_color(color: tuple) -> str:
    """Name a color

    Args:
        color (tuple[int, int, int]): RGB color tuple

    Returns:
        str: Name of color
    """
    hex = "%02x%02x%02x" % (color[0], color[1], color[2])
    colors = {
        "ff0000" : "red",
        "00ff00" : "green",
        "0000ff" : "blue",
        "ffff00" : "yellow",
        "ff00ff" : "purple",
        "00ffff" : "cyan",
        "000000" : "black",
        "ffffff" : "white"
    }
    if hex not in colors:
        return "unknown"
    else:
        return colors[hex]


def main():
    global config
    parser = argparse.ArgumentParser(description="This script pulls data form the inofficial Trafikverket reoad condition API and analyzes the road condition color tiles")
    parser.add_argument("-v", "--verbose", help="Increase output verbosity", action="store_true")
    parser.add_argument("-c", "--config", action="store", help="Configuration file", default="sampleconfig.yml")
    args = parser.parse_args()

    config = configparser.ConfigParser()
    try:
        config.read(args.config, encoding='utf-8')
    except Exception as e:
        logging.error("Failed to read config file: %s" % str(e))
        sys.exit(1)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, stream=sys.stdout,
                        format='%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s',
                        datefmt='%Y%m%d %H:%M:%S')
    logging.info("---[ Starting %s ]---------------------------------------------" % sys.argv[0])

    broker = config["MQTT"]["MQTTBroker"]
    retain = "Retain" in config["MQTT"] and "True" in config["MQTT"]["Retain"]
    topic = config["MQTT"]["MQTTRoadConditionTopic"]

    warned: bool = False
    while not mqttwrapper.is_connected():
        try:
            mqttwrapper.run_script(mqtt_callback, broker=broker, topics=["/nada"], retain=retain, blocking=False)
        except socket.gaierror:
            if not warned:
                logging.warning(f"Failed to connect to MQTT broker at {broker}, will retry")
                warned = True
            time.sleep(1)
    logging.info(f"Connected to MQTT broker at {broker}")

    for i in range(0, 10):
        key = "Condition%d" % (i)
        if key in config:
            level = config[key]["ZoomLevel"]
            x = config[key]["TileX"]
            y = config[key]["TileY"]
            road_name = config[key]["RoadName"]
            try:
                url = "https://maps.trafikinfo.trafikverket.se/TMS/.TMS/1.0.0/LPV/%s/%s/%s.png" % (level, x, y)
                image = get_image(url)
                color = get_prominent_color(image)
                color_prim = color_saturate(color)
                color_name = name_color(color_prim)
                logging.info("Most prominent color for %s is %s #%02x%02x%02x -> #%02x%02x%02x" % (road_name, color_name, color[0], color[1], color[2], color_prim[0], color_prim[1], color_prim[2]))
            except Exception as e:
                logging.error("Condition processing caused exception", exc_info=True)
                color_name = "_"
            color_hex = "%02x%02x%02x" % (color_prim[0], color_prim[1], color_prim[2])
            mqttwrapper.publish(topic + "/" + road_name + "/color", color_hex, retain)
            mqttwrapper.publish(topic + "/" + road_name + "/condition", color_name, retain)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error("Exception occurred in main", exc_info=True)
