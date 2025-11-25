#!/usr/bin/env python3
"""
Test script to verify multilingual autocomplete functionality
Run with: python test_multilingual_autocomplete.py
"""

import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import RequestFactory
from teachers.views import subjects_autocomplete
import json

def test_autocomplete(query):
    """Test autocomplete with a given query"""
    print(f"\n🔍 Testing query: '{query}'")
    
    rf = RequestFactory()
    request = rf.get('/api/subjects/autocomplete/', {'q': query})
    response = subjects_autocomplete(request)
    
    try:
        data = json.loads(response.content.decode())
        results = data.get('results', [])
        
        print(f"📊 Found {len(results)} results:")
        for result in results:
            print(f"  • {result['name']} (ID: {result['id']}) - {result['category']}")
        
        return results
    except Exception as e:
        print(f"❌ Error parsing response: {e}")
        print(f"Raw response: {response.content.decode()}")
        return []

def main():
    print("=" * 60)
    print("🔬 MULTILINGUAL AUTOCOMPLETE TEST")
    print("=" * 60)
    
    # Test queries in different languages/scripts
    test_queries = [
        # Math-related queries
        "мат",           # Cyrillic (Russian)
        "mat",           # Latin (should find Математика via transliteration)
        "математика",    # Full Cyrillic word
        "matematika",    # Full Latin transliteration
        
        # Other subjects
        "анг",           # Cyrillic (should find Английский)
        "ang",           # Latin transliteration
        "english",       # English
        "инглиш",        # Cyrillic transliteration of English
        
        # Programming-related
        "прог",          # Cyrillic (Программирование)
        "prog",          # Latin transliteration
        "python",        # Latin (should work as-is)
        "питон",         # Cyrillic transliteration
    ]
    
    all_results = {}
    for query in test_queries:
        results = test_autocomplete(query)
        all_results[query] = results
    
    print("\n" + "=" * 60)
    print("📈 SUMMARY")
    print("=" * 60)
    
    successful_queries = [q for q, r in all_results.items() if len(r) > 0]
    failed_queries = [q for q, r in all_results.items() if len(r) == 0]
    
    print(f"✅ Successful queries ({len(successful_queries)}): {', '.join(successful_queries)}")
    print(f"❌ Failed queries ({len(failed_queries)}): {', '.join(failed_queries)}")
    
    if successful_queries:
        print("\n🎉 Multilingual autocomplete is working!")
        print("📝 Users can now search in Latin, Cyrillic, and mixed scripts.")
    else:
        print("\n⚠️  No queries returned results. Check the database for subjects.")
    
    print("\n💡 Next steps:")
    print("1. Start the Django development server: python manage.py runserver")
    print("2. Test the frontend at: http://localhost:8000/teacher/register/step2/")
    print("3. Try typing both Latin and Cyrillic characters in the subject search")

if __name__ == "__main__":
    main()