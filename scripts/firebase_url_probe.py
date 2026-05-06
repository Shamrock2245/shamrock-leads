#!/usr/bin/env python3
"""
Probe the Firebase Firestore database to find what document
BlueBubbles writes its server URL to.
"""
import json, sys, os
import firebase_admin
from firebase_admin import credentials, firestore

CRED_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "firebase-adminsdk.json")

def main():
    cred = credentials.Certificate(CRED_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    print("=== Listing all Firestore collections ===")
    collections = db.collections()
    for col in collections:
        print(f"\nCollection: {col.id}")
        docs = col.limit(5).stream()
        for doc in docs:
            print(f"  Doc: {doc.id}")
            data = doc.to_dict()
            print(f"  Data: {json.dumps(data, indent=4, default=str)[:500]}")

if __name__ == "__main__":
    main()
