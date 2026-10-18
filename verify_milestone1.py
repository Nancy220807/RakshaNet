import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from db import db_conn
from contacts import mask_phone

with db_conn() as conn:
    print("=== SEEDED DEMO CONTACTS ===")
    contacts = conn.execute("""
        SELECT tc.user_id, u.name as user_name, tc.name, tc.phone, tc.relation 
        FROM trusted_contacts tc 
        JOIN users u ON tc.user_id = u.id 
        ORDER BY tc.user_id, tc.id
    """).fetchall()
    for row in contacts:
        print(f"User: {row['user_name']} ({row['user_id']}) -> Contact: {row['name']} | Phone: {mask_phone(row['phone'])} | Relation: {row['relation']}")
        
    print("\n=== SEEDED DEMO RESPONDERS ===")
    responders = conn.execute("""
        SELECT u.id, u.name, u.phone, u.is_responder, u.responder_verified, rl.lat, rl.lon 
        FROM users u 
        JOIN responder_locations rl ON u.id = rl.user_id
    """).fetchall()
    for row in responders:
        print(f"Responder: {row['name']} ({row['id']}) | Phone: {mask_phone(row['phone'])} | Verified: {bool(row['responder_verified'])} | Loc: ({row['lat']}, {row['lon']})")
