import os
import time
import threading
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from queue import Queue
from datetime import datetime
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager  # Changed import
import shutil

# Remove the pre-computed cache path section and replace with:
driver = None
driver_lock = threading.Lock()

app = Flask(__name__)
app.secret_key = "super_secret_key"   # for flash messages

# Global message queue for logs
message_queue = Queue()

# ----------------------------------------------------------------------
# Global driver – keeps the same WhatsApp session for the whole run
driver = None
driver_lock = threading.Lock()


def log_message(message):
    """Add timestamp and push message to queue"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message_queue.put(f"[{timestamp}] {message}")


def init_driver():
    """Create (or reuse) a Chrome instance that keeps the WhatsApp login."""
    global driver
    with driver_lock:
        if driver is not None:
            return driver

        options = webdriver.ChromeOptions()
        # Keep your WhatsApp login between restarts
        options.add_argument(f"--user-data-dir={os.path.abspath('./User_Data')}")
        # Recommended flags
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

        # Enable headless if HEADLESS=1 in env (optional)
        if os.environ.get("HEADLESS") == "1":
            options.add_argument("--headless=new")

        # Try to find Chrome/Chromium binary automatically
        chrome_bin = os.environ.get("CHROME_BIN") or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser") or "/usr/bin/google-chrome"
        if chrome_bin and os.path.exists(chrome_bin):
            options.binary_location = chrome_bin

        # Use webdriver-manager to install and provide chromedriver executable
        service = Service(ChromeDriverManager().install(), log_path="chromedriver.log")

        try:
            driver = webdriver.Chrome(service=service, options=options)
        except Exception as e:
            log_message(f"init_driver error: {e}")
            # attach last lines of chromedriver.log to logs for diagnosis
            try:
                if os.path.exists("chromedriver.log"):
                    with open("chromedriver.log", "r") as f:
                        lines = f.readlines()
                        for line in lines[-200:]:
                            log_message(line.rstrip())
            except Exception:
                pass
            raise

        driver.get("https://web.whatsapp.com")
        return driver


def close_driver():
    global driver
    with driver_lock:
        if driver:
            driver.quit()
            driver = None


# ----------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # 1. Get the uploaded CSV
        if "csv_file" not in request.files:
            flash("No file part")
            return redirect(request.url)
        file = request.files["csv_file"]
        if file.filename == "":
            flash("No selected file")
            return redirect(request.url)

        # 2. Get the message
        message = request.form.get("message", "").strip()
        if not message:
            flash("Please type a message")
            return redirect(request.url)

        # 3. Save CSV temporarily
        csv_path = os.path.join("uploads", file.filename)
        os.makedirs("uploads", exist_ok=True)
        file.save(csv_path)

        # 4. Run the bulk-send in a background thread (so the page stays responsive)
        thread = threading.Thread(target=bulk_send, args=(csv_path, message))
        thread.start()
        flash("Bulk sending started – check the server console for progress.")
        return redirect(url_for("index"))

    return render_template("index.html")


@app.route("/logs")
def get_logs():
    """Endpoint to retrieve accumulated logs"""
    logs = []
    while not message_queue.empty():
        logs.append(message_queue.get())
    return jsonify(logs)


# ----------------------------------------------------------------------
def bulk_send(csv_path: str, message: str):
    """Core logic – same as your original script, but message is fixed."""
    try:
        # ----- read CSV -------------------------------------------------
        df = pd.read_csv(csv_path)
        log_message(f"Loaded {len(df)} contacts")
        log_message(f"Columns: {list(df.columns)}")

        # ----- auto-detect phone column ---------------------------------
        phone_col = None
        for col in df.columns:
            if col.strip().lower() in ["phone", "number", "mobile", "contact", "phno"]:
                phone_col = col
                break
        if not phone_col:
            log_message("No phone column found!")
            return

        # ----- start Selenium -------------------------------------------
        driver = init_driver()
        wait = WebDriverWait(driver, 60)

        # wait for WhatsApp Web to be ready (QR scan if needed)
        wait.until(
            EC.presence_of_element_located(
                (By.XPATH, '//div[@contenteditable="true"][@data-tab="3"]')
            )
        )
        log_message("WhatsApp ready – starting to send...")

        for idx, row in df.iterrows():
            raw_phone = str(row[phone_col]).strip()
            if not raw_phone.startswith("+"):
                phone = "+" + raw_phone
            else:
                phone = raw_phone

            log_message(f"Sending to {phone} ...")
            try:
                # Open direct chat URL
                driver.get(f"https://web.whatsapp.com/send?phone={phone[1:]}")
                time.sleep(6)   # wait for chat to load

                input_box = wait.until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]')
                    )
                )
                input_box.clear()
                input_box.send_keys(message + Keys.ENTER)
                log_message("Sent")
                time.sleep(12)   # safety delay between messages
            except Exception as e:
                log_message(f"Failed for {phone}: {e}")

        log_message("All done!")
    except Exception as e:
        log_message(f"Bulk send error: {e}")
    finally:
        # Clean up after process is complete
        try:
            # Close the driver first
            close_driver()
            
            # Remove the uploads folder
            if os.path.exists("uploads"):
                shutil.rmtree("uploads")
                log_message("Cleaned up uploads folder")
            
            # Remove User_Data folder
            if os.path.exists("User_Data"):
                shutil.rmtree("User_Data")
                log_message("Cleaned up User_Data folder")
        except Exception as e:
            log_message(f"Cleanup error: {e}")


# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Create uploads folder if missing
    os.makedirs("uploads", exist_ok=True)
    app.run(debug=True, host='0.0.0.0', port=5222)