from flask import Flask, render_template, request, jsonify
import json
from datetime import datetime
import os
import webbrowser
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import threading
import csv
import io

app = Flask(__name__)

# Supported browsers
browsers = ["Chrome", "Firefox", "Edge"]
activity_log = []
current_driver = None
driver_lock = threading.Lock()

# File to store activity data
DATA_FILE = "activity_log.json"

# Load existing activity log
def load_activity_log():
    global activity_log
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            activity_log = json.load(f)

# Save activity log
def save_activity_log():
    with open(DATA_FILE, 'w') as f:
        json.dump(activity_log, f, indent=2)

# Initialize activity log
load_activity_log()

# Function to track navigation
def track_navigation(driver, browser_name):
    previous_url = driver.current_url
    while True:
        with driver_lock:
            if not driver:
                break
        try:
            current_url = driver.current_url
            if current_url != previous_url:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                activity = {
                    'url': current_url,
                    'browser': browser_name,
                    'timestamp': timestamp
                }
                activity_log.append(activity)
                save_activity_log()
                previous_url = current_url
        except:
            break
        import time
        time.sleep(1)  # Check every second

# Function to open browser and track activity
def open_browser(browser_name, url="https://www.google.com", incognito=False):
    global current_driver
    try:
        with driver_lock:
            if current_driver:
                current_driver.quit()
                current_driver = None

        options = None
        driver = None
        if browser_name == "Chrome":
            from selenium.webdriver.chrome.options import Options
            options = Options()
            if incognito:
                options.add_argument("--incognito")
            service = ChromeService()
            driver = webdriver.Chrome(service=service, options=options)
        elif browser_name == "Firefox":
            from selenium.webdriver.firefox.options import Options
            options = Options()
            if incognito:
                options.add_argument("-private")
            service = FirefoxService()
            driver = webdriver.Firefox(service=service, options=options)
        elif browser_name == "Edge":
            from selenium.webdriver.edge.options import Options
            options = Options()
            if incognito:
                options.add_argument("--inPrivate")
            service = EdgeService()
            driver = webdriver.Edge(service=service, options=options)
        else:
            webbrowser.open(url)
            return None

        driver.get(url)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        activity = {
            'url': url,
            'browser': browser_name,
            'timestamp': timestamp,
            'incognito': incognito
        }
        activity_log.append(activity)
        save_activity_log()

        with driver_lock:
            current_driver = driver

        # Start tracking navigation in a separate thread
        threading.Thread(target=track_navigation, args=(driver, browser_name), daemon=True).start()
        return driver

    except Exception as e:
        print(f"Error opening {browser_name}: {str(e)}")
        webbrowser.open(url)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        activity = {
            'url': url,
            'browser': browser_name,
            'timestamp': timestamp,
            'error': str(e),
            'incognito': incognito
        }
        activity_log.append(activity)
        save_activity_log()
        return None

@app.route('/')
def index():
    return render_template('index.html', browsers=browsers)

@app.route('/start', methods=['POST'])
def start():
    browser = request.form.get('browser')
    url = request.form.get('url', 'https://www.google.com')
    incognito = request.form.get('incognito') == 'true'
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    threading.Thread(target=open_browser, args=(browser, url, incognito)).start()
    
    return jsonify({
        'status': 'success',
        'browser': browser,
        'url': url,
        'message': f'Opening {url} in {browser}{" (Incognito)" if incognito else ""}'
    })

@app.route('/stop', methods=['POST'])
def stop():
    global current_driver
    with driver_lock:
        if current_driver:
            try:
                current_driver.quit()
                current_driver = None
                return jsonify({
                    'status': 'success',
                    'message': 'Browser closed'
                })
            except Exception as e:
                return jsonify({
                    'status': 'error',
                    'message': f'Error closing browser: {str(e)}'
                })
        else:
            return jsonify({
                'status': 'error',
                'message': 'No browser is open'
            })

@app.route('/history')
def get_history():
    return jsonify(activity_log)

@app.route('/delete_history', methods=['POST'])
def delete_history():
    global activity_log
    index = int(request.form.get('index'))
    if 0 <= index < len(activity_log):
        activity_log.pop(index)
        save_activity_log()
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Invalid index'})

@app.route('/download_history', methods=['GET'])
def download_history():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Browser', 'URL', 'Incognito', 'Error'])
    for activity in activity_log:
        writer.writerow([
            activity['timestamp'],
            activity['browser'],
            activity['url'],
            activity.get('incognito', False),
            activity.get('error', '')
        ])
    output.seek(0)
    return app.response_class(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=history.csv'}
    )

if __name__ == '__main__':
    app.run(debug=True)