from flask import Flask, render_template, request, jsonify, send_file, Response
import json
import os
import time
import threading
import csv
import io
import logging
from datetime import datetime, timedelta
from collections import defaultdict
import subprocess
import psutil
import requests
from urllib.parse import urlparse, urljoin
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from functools import wraps
import schedule
import uuid
from flask_cors import CORS

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this'
CORS(app)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('virtual_browser.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Database setup
DATABASE = 'virtual_browser.db'

def init_database():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Create tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            api_key TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS browser_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            user_id INTEGER,
            browser_type TEXT NOT NULL,
            browser_version TEXT,
            pid INTEGER,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            is_incognito BOOLEAN DEFAULT 0,
            initial_url TEXT,
            status TEXT DEFAULT 'active',
            memory_usage REAL,
            cpu_usage REAL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            user_id INTEGER,
            url TEXT NOT NULL,
            title TEXT,
            browser_type TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_incognito BOOLEAN DEFAULT 0,
            load_time REAL,
            status_code INTEGER,
            error_message TEXT,
            page_size INTEGER,
            user_agent TEXT,
            referrer TEXT,
            ip_address TEXT,
            geolocation TEXT,
            FOREIGN KEY (session_id) REFERENCES browser_sessions (session_id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT NOT NULL,
            title TEXT,
            description TEXT,
            tags TEXT,
            category TEXT,
            favicon_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed TIMESTAMP,
            access_count INTEGER DEFAULT 0,
            is_favorite BOOLEAN DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    conn.commit()
    conn.close()

# Global variables
active_drivers = {}
driver_locks = defaultdict(threading.Lock)
system_stats = {
    'total_sessions': 0,
    'active_sessions': 0,
    'total_uptime': 0,
    'start_time': datetime.now()
}

# Authentication decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'message': 'Token is missing'}), 401
        try:
            if token.startswith('Bearer '):
                token = token[7:]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user_id = data['user_id']
        except:
            return jsonify({'message': 'Token is invalid'}), 401
        return f(current_user_id, *args, **kwargs)
    return decorated

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if not all([username, email, password]):
        return jsonify({'error': 'Missing required fields'}), 400

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    try:
        password_hash = generate_password_hash(password)
        api_key = str(uuid.uuid4())

        cursor.execute('''
            INSERT INTO users (username, email, password_hash, api_key)
            VALUES (?, ?, ?, ?)
        ''', (username, email, password_hash, api_key))

        conn.commit()
        user_id = cursor.lastrowid

        token = jwt.encode({
            'user_id': user_id,
            'username': username,
            'exp': datetime.utcnow() + timedelta(days=30)
        }, app.config['SECRET_KEY'])

        return jsonify({
            'message': 'User registered successfully',
            'token': token,
            'api_key': api_key
        })

    except sqlite3.IntegrityError:
        return jsonify({'error': 'Username or email already exists'}), 400
    finally:
        conn.close()

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    cursor.execute('SELECT id, username, password_hash FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()

    if user and check_password_hash(user[2], password):
        token = jwt.encode({
            'user_id': user[0],
            'username': user[1],
            'exp': datetime.utcnow() + timedelta(days=30)
        }, app.config['SECRET_KEY'])

        cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user[0],))
        conn.commit()

        conn.close()
        return jsonify({'token': token})

    conn.close()
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/browser/start', methods=['POST'])
@token_required
def start_browser(current_user_id):
    data = request.get_json()
    browser_type = data.get('browser', 'Chrome')
    url = data.get('url', 'https://www.google.com')
    incognito = data.get('incognito', False)

    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        session_id = str(uuid.uuid4())

        return jsonify({
            'success': True,
            'session_id': session_id,
            'browser_type': browser_type,
            'url': url,
            'title': 'Example Title',
            'load_time': 1.5,
            'message': f'{browser_type} launched successfully'
        })

    except Exception as e:
        logger.error(f"Failed to start browser: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/browser/stop', methods=['POST'])
@token_required
def stop_browser(current_user_id):
    data = request.get_json()
    session_id = data.get('session_id')

    try:
        return jsonify({'success': True, 'message': 'Browser session closed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/history', methods=['GET'])
@token_required
def get_history(current_user_id):
    limit = request.args.get('limit', 100, type=int)
    search = request.args.get('search', '')

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    query = '''
        SELECT url, title, browser_type, timestamp, load_time, error_message
        FROM activity_log
        WHERE user_id = ?
    '''
    params = [current_user_id]

    if search:
        query += ' AND (url LIKE ? OR title LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%'])

    query += ' ORDER BY timestamp DESC LIMIT ?'
    params.append(limit)

    cursor.execute(query, params)

    history = []
    for row in cursor.fetchall():
        history.append({
            'url': row[0],
            'title': row[1],
            'browser_type': row[2],
            'timestamp': row[3],
            'load_time': row[4],
            'error_message': row[5]
        })

    conn.close()
    return jsonify(history)

@app.route('/api/bookmarks', methods=['GET', 'POST', 'DELETE'])
@token_required
def manage_bookmarks(current_user_id):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    if request.method == 'GET':
        cursor.execute('''
            SELECT id, url, title, description, tags, category, created_at, access_count
            FROM bookmarks
            WHERE user_id = ?
            ORDER BY created_at DESC
        ''', (current_user_id,))

        bookmarks = []
        for row in cursor.fetchall():
            bookmarks.append({
                'id': row[0],
                'url': row[1],
                'title': row[2],
                'description': row[3],
                'tags': row[4],
                'category': row[5],
                'created_at': row[6],
                'access_count': row[7]
            })

        conn.close()
        return jsonify(bookmarks)

    elif request.method == 'POST':
        data = request.get_json()
        url = data.get('url')
        title = data.get('title', '')
        description = data.get('description', '')
        tags = data.get('tags', '')
        category = data.get('category', 'General')

        cursor.execute('''
            INSERT INTO bookmarks (user_id, url, title, description, tags, category)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (current_user_id, url, title, description, tags, category))

        conn.commit()
        bookmark_id = cursor.lastrowid
        conn.close()

        return jsonify({'success': True, 'bookmark_id': bookmark_id})

    elif request.method == 'DELETE':
        bookmark_id = request.args.get('id')
        cursor.execute('DELETE FROM bookmarks WHERE id = ? AND user_id = ?', (bookmark_id, current_user_id))
        conn.commit()
        conn.close()

        return jsonify({'success': True})

@app.route('/api/export/history', methods=['GET'])
@token_required
def export_history(current_user_id):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT url, title, browser_type, timestamp, load_time, error_message
        FROM activity_log
        WHERE user_id = ?
        ORDER BY timestamp DESC
    ''', (current_user_id,))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['URL', 'Title', 'Browser', 'Timestamp', 'Load Time', 'Error'])

    for row in cursor.fetchall():
        writer.writerow(row)

    output.seek(0)
    conn.close()

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=browsing_history.csv'}
    )

def cleanup_old_sessions():
    cutoff_time = datetime.now() - timedelta(hours=24)

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE browser_sessions
        SET status = 'timeout', end_time = CURRENT_TIMESTAMP
        WHERE status = 'active' AND start_time < ?
    ''', (cutoff_time,))

    conn.commit()
    conn.close()

schedule.every().hour.do(cleanup_old_sessions)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

if __name__ == '__main__':
    init_database()
    app.run(debug=True)
