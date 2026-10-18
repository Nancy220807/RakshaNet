"""
contacts.py — Trusted Contacts API for RakshaNet Smart SOS (Phase 3)
Endpoints for managing up to 3 emergency contacts per user.
"""

import re
import logging
from flask import Blueprint, request, jsonify, session
from db import db_conn

logger = logging.getLogger(__name__)

contacts_bp = Blueprint("contacts", __name__)

def _error(msg, code=400):
    return jsonify({"error": msg}), code

def mask_phone(phone: str) -> str:
    """Mask phone number, preserving last 4 digits and country code prefix if present."""
    if not phone:
        return ""
    clean = str(phone).strip()
    if len(clean) <= 4:
        return "****"
    return clean[:3] + "*" * (len(clean) - 7) + clean[-4:] if len(clean) > 7 else "*" * (len(clean) - 4) + clean[-4:]

def normalize_phone(raw: str) -> str:
    """
    Normalize phone to E.164 standard.
    Assumes Indian (+91) country code if 10 digits provided without leading +.
    Returns normalized string or None if invalid.
    """
    if not raw:
        return None
    # Strip spaces, dashes, dots, parentheses
    s = re.sub(r"[\s\-\.\(\)]", "", str(raw).strip())
    if not s:
        return None
    
    # 10 digits: assume +91
    if re.fullmatch(r"\d{10}", s):
        return f"+91{s}"
    
    # Starts with 0 followed by 10 digits (common local format)
    if re.fullmatch(r"0\d{10}", s):
        return f"+91{s[1:]}"
    
    # Already has +
    if s.startswith("+"):
        digits = s[1:]
        if digits.isdigit() and 7 <= len(digits) <= 15:
            return s
        return None
    
    # If 12 digits starting with 91
    if s.startswith("91") and len(s) == 12 and s.isdigit():
        return f"+{s}"
        
    return None

def sanitize_name(name: str) -> str:
    """Sanitize and clamp name string."""
    if not name:
        return ""
    cleaned = re.sub(r"[<>]", "", str(name).strip())
    return cleaned[:50]

def get_current_user_id():
    """Get authenticated user ID from session or DEMO_MODE fallback."""
    uid = session.get("user_id")
    if uid:
        return uid
    # In demo mode, fallback to default user_asha if session not set
    from config import DEMO_MODE
    if DEMO_MODE:
        return "user_asha"
    return None

@contacts_bp.route("/api/contacts", methods=["GET"])
def list_contacts():
    user_id = get_current_user_id()
    if not user_id:
        return _error("Authentication required.", 401)
    
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, phone, relation, created_at
            FROM trusted_contacts
            WHERE user_id = ?
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()
        
    contacts = []
    for r in rows:
        d = dict(r)
        d["phone_masked"] = mask_phone(d["phone"])
        contacts.append(d)
        
    return jsonify({
        "status": "ok",
        "contacts": contacts,
        "count": len(contacts),
        "max_allowed": 3,
    })

@contacts_bp.route("/api/contacts", methods=["POST"])
def add_contact():
    user_id = get_current_user_id()
    if not user_id:
        return _error("Authentication required.", 401)
        
    data = request.get_json(silent=True) or {}
    raw_name = data.get("name")
    raw_phone = data.get("phone")
    raw_relation = data.get("relation", "Other")
    
    if not raw_name or not str(raw_name).strip():
        return _error("Contact name is required.", 400)
    if not raw_phone or not str(raw_phone).strip():
        return _error("Phone number is required.", 400)
        
    name = sanitize_name(raw_name)
    if len(name) < 2:
        return _error("Contact name must be at least 2 characters.", 400)
        
    phone = normalize_phone(raw_phone)
    if not phone:
        return _error("Invalid phone number. Please provide a valid 10-digit number or E.164 format (e.g. +919876543210).", 400)
        
    relation = re.sub(r"[<>]", "", str(raw_relation).strip())[:30] or "Other"
    
    from config import SOS_MAX_CONTACTS
    max_c = SOS_MAX_CONTACTS
    
    with db_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM trusted_contacts WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        
        if count >= max_c:
            return _error(f"Limit reached: You can have at most {max_c} trusted contacts.", 400)
            
        existing = conn.execute(
            "SELECT id FROM trusted_contacts WHERE user_id = ? AND phone = ?",
            (user_id, phone),
        ).fetchone()
        if existing:
            return _error("A contact with this phone number already exists.", 409)
            
        cursor = conn.execute(
            """
            INSERT INTO trusted_contacts (user_id, name, phone, relation)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, name, phone, relation),
        )
        new_id = cursor.lastrowid
        
    return jsonify({
        "status": "ok",
        "message": "Trusted contact added successfully.",
        "contact": {
            "id": new_id,
            "name": name,
            "phone": phone,
            "phone_masked": mask_phone(phone),
            "relation": relation,
        }
    }), 201

@contacts_bp.route("/api/contacts/<int:contact_id>", methods=["DELETE"])
def delete_contact(contact_id):
    user_id = get_current_user_id()
    if not user_id:
        return _error("Authentication required.", 401)
        
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id FROM trusted_contacts WHERE id = ? AND user_id = ?",
            (contact_id, user_id),
        ).fetchone()
        if not row:
            return _error("Contact not found or does not belong to you.", 404)
            
        conn.execute("DELETE FROM trusted_contacts WHERE id = ?", (contact_id,))
        
    return jsonify({
        "status": "ok",
        "message": "Contact removed successfully.",
    })
