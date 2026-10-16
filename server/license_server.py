from flask import Flask, request, jsonify
import sqlite3
import secrets
import hashlib
from datetime import datetime, timedelta
import os

app = Flask(__name__)

# ========== CONFIG ==========
ADMIN_KEY = os.environ.get('ADMIN_KEY', 'PEXURA_ADMIN_2025_SECRET_CHANGE_THIS')

# Data directory for persistent storage
DATA_DIR = os.environ.get('DATA_DIR', '/opt/render/project/src/data')
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    DB_FILE = os.path.join(DATA_DIR, "licenses.db")
except Exception as e:
    print(f"⚠️ Could not create DATA_DIR: {e}")
    DB_FILE = "licenses.db"

print(f"📁 Database file: {DB_FILE}")

# ========== DATABASE ==========
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS licenses
                 (key TEXT PRIMARY KEY, 
                  hwid TEXT, 
                  expires TEXT, 
                  created TEXT, 
                  active INTEGER DEFAULT 1,
                  owner TEXT,
                  last_seen TEXT)''')
    conn.commit()
    conn.close()
    print("✅ Database initialized")

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# ========== ROUTES ==========

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "service": "Pexura License Server",
        "version": "1.0"
    })

@app.route('/verify', methods=['POST'])
def verify():
    """First-time activation"""
    try:
        data = request.json
        key = data.get('license_key', '').strip()
        hwid = data.get('hardware_id', '').strip()
        product = data.get('product', 'pexura-rambler-checker')
        
        if not key or not hwid:
            return jsonify({"valid": False, "message": "Missing key or HWID"}), 400
        
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM licenses WHERE key = ?", (key,))
        result = c.fetchone()
        
        if not result:
            conn.close()
            return jsonify({"valid": False, "message": "License key not found"})
        
        # Check active
        if not result['active']:
            conn.close()
            return jsonify({"valid": False, "message": "License deactivated"})
        
        # Check expiration
        if result['expires']:
            exp_date = datetime.fromisoformat(result['expires'])
            if exp_date < datetime.now():
                conn.close()
                return jsonify({"valid": False, "message": "License expired"})
        
        # First activation - bind to HWID
        if not result['hwid']:
            c.execute("UPDATE licenses SET hwid = ?, last_seen = ? WHERE key = ?",
                     (hwid, datetime.now().isoformat(), key))
            conn.commit()
            message = "License activated successfully"
        elif result['hwid'] != hwid:
            conn.close()
            return jsonify({
                "valid": False, 
                "message": "License already bound to another machine"
            })
        else:
            c.execute("UPDATE licenses SET last_seen = ? WHERE key = ?",
                     (datetime.now().isoformat(), key))
            conn.commit()
            message = "License verified"
        
        # Generate session token
        token = hashlib.sha256(f"{key}{hwid}{secrets.token_hex(16)}".encode()).hexdigest()
        
        conn.close()
        
        return jsonify({
            "valid": True,
            "message": message,
            "expires": result['expires'],
            "token": token,
            "owner": result['owner'] or "Customer"
        })
    
    except Exception as e:
        return jsonify({"valid": False, "message": f"Server error: {str(e)}"}), 500

@app.route('/check', methods=['POST'])
def check():
    """Re-check license (called on each startup)"""
    try:
        data = request.json
        key = data.get('license_key', '').strip()
        hwid = data.get('hardware_id', '').strip()
        token = data.get('token', '')
        
        if not key or not hwid:
            return jsonify({"valid": False, "message": "Missing data"}), 400
        
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM licenses WHERE key = ?", (key,))
        result = c.fetchone()
        
        if not result:
            conn.close()
            return jsonify({"valid": False, "message": "License not found"})
        
        if not result['active']:
            conn.close()
            return jsonify({"valid": False, "message": "License deactivated"})
        
        if result['hwid'] != hwid:
            conn.close()
            return jsonify({"valid": False, "message": "HWID mismatch"})
        
        if result['expires']:
            exp_date = datetime.fromisoformat(result['expires'])
            if exp_date < datetime.now():
                conn.close()
                return jsonify({"valid": False, "message": "License expired"})
        
        # Update last seen
        c.execute("UPDATE licenses SET last_seen = ? WHERE key = ?",
                 (datetime.now().isoformat(), key))
        conn.commit()
        
        # Calculate days left
        days_left = None
        if result['expires']:
            exp_date = datetime.fromisoformat(result['expires'])
            days_left = (exp_date - datetime.now()).days
        
        conn.close()
        
        return jsonify({
            "valid": True,
            "message": "License valid",
            "days_left": days_left,
            "expires": result['expires']
        })
    
    except Exception as e:
        return jsonify({"valid": False, "message": f"Server error: {str(e)}"}), 500

@app.route('/generate', methods=['POST'])
def generate():
    """Admin: Generate new license keys"""
    admin_key = request.headers.get('X-Admin-Key', '')
    
    if admin_key != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.json
        days = int(data.get('days', 30))
        owner = data.get('owner', 'Customer')
        count = int(data.get('count', 1))
        
        keys = []
        conn = get_db()
        c = conn.cursor()
        
        for _ in range(count):
            key = f"PEXURA-{secrets.token_hex(8).upper()}"
            expires = (datetime.now() + timedelta(days=days)).isoformat()
            
            c.execute("INSERT INTO licenses (key, expires, created, owner) VALUES (?, ?, ?, ?)",
                     (key, expires, datetime.now().isoformat(), owner))
            
            keys.append({
                "key": key,
                "expires": expires,
                "days": days,
                "owner": owner
            })
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "licenses": keys})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/list', methods=['GET'])
def list_licenses():
    """Admin: List all licenses"""
    admin_key = request.headers.get('X-Admin-Key', '')
    
    if admin_key != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM licenses ORDER BY created DESC")
    results = c.fetchall()
    conn.close()
    
    licenses = []
    for r in results:
        licenses.append({
            "key": r['key'],
            "hwid": r['hwid'],
            "expires": r['expires'],
            "created": r['created'],
            "active": bool(r['active']),
            "owner": r['owner'],
            "last_seen": r['last_seen']
        })
    
    return jsonify({"total": len(licenses), "licenses": licenses})

@app.route('/revoke', methods=['POST'])
def revoke():
    """Admin: Revoke a license"""
    admin_key = request.headers.get('X-Admin-Key', '')
    
    if admin_key != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    key = data.get('key')
    
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE licenses SET active = 0 WHERE key = ?", (key,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    
    return jsonify({"success": affected > 0, "message": f"Revoked {affected} license(s)"})

# ========== MAIN ==========
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    print("="*60)
    print("🔐 Pexura License Server")
    print("="*60)
    print(f"Admin Key: {ADMIN_KEY}")
    print(f"Port: {port}")
    print(f"DB File: {DB_FILE}")
    print("="*60)
    
    app.run(host='0.0.0.0', port=port, debug=False)