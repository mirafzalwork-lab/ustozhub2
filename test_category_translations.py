#!/usr/bin/env python3
"""
Test script to verify category translations in autocomplete API
Run with: python test_category_translations.py
"""

import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import RequestFactory
from django.utils import translation
from teachers.views import subjects_autocomplete, subjects_categories
import json

def test_translations():
    print("=" * 60)
    print("🔬 CATEGORY TRANSLATIONS TEST")
    print("=" * 60)
    
    rf = RequestFactory()
    
    # Test languages
    languages = ['ru', 'uz', 'en']
    
    for lang in languages:
        print(f"\n🌐 Testing language: {lang.upper()}")
        print("=" * 40)
        
        # Activate language
        translation.activate(lang)
        
        # Test autocomplete
        req = rf.get('/api/subjects/autocomplete/', {'q': 'мат'})
        resp = subjects_autocomplete(req)
        data = json.loads(resp.content.decode())
        
        print(f"📝 Autocomplete results for 'мат':")
        for result in data['results']:
            print(f"  • {result['name']} → {result['category']}")
        
        # Test categories
        req = rf.get('/api/subjects/categories/')
        resp = subjects_categories(req)
        data = json.loads(resp.content.decode())
        
        print(f"📁 Categories:")
        for result in data['results'][:5]:  # Show first 5
            print(f"  • {result['name']} ({result['subjects_count']} subjects)")
    
    # Deactivate translation
    translation.deactivate()
    
    print("\n" + "=" * 60)
    print("✅ Translation test completed!")
    print("💡 Now test in browser:")
    print("   Russian: http://localhost:8000/ru/register/step2/")
    print("   Uzbek:   http://localhost:8000/uz/register/step2/")
    print("   English: http://localhost:8000/en/register/step2/")

if __name__ == "__main__":
    test_translations()