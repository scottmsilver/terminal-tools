import json
import requests
import base64
import subprocess
import os

def take_screenshot(output_path="screenshot.jpg"):
    # Using 'import' from ImageMagick
    subprocess.run(["import", "-window", "root", output_path])
    return output_path

def describe_screenshot(image_path):
    with open(image_path, "rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode('utf-8')
    
    prompt = "Look at this screenshot of a Linux i3 desktop. What are the main applications open? What is the user working on? Keep it very concise (1 sentence)."
    
    try:
        response = requests.post("http://localhost:11434/api/generate", json={
            "model": "gemma3:4b",
            "prompt": prompt,
            "images": [encoded_image],
            "stream": False
        })
        response.raise_for_status()
        return response.json()["response"].strip()
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    img = take_screenshot()
    description = describe_screenshot(img)
    print(f"Visual Analysis: {description}")
    os.remove(img)
