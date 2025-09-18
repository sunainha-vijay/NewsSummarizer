import json
import boto3
import requests
from datetime import datetime, timedelta
import hashlib
import re
from bs4 import BeautifulSoup
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb')
cache_table = dynamodb.Table('news-summarizer-cache')

def lambda_handler(event, context):
    """
    AWS Lambda handler for news summarization
    """
    try:
        # ✅ Handle CORS preflight
        if event.get("httpMethod") == "OPTIONS":
            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type"
                },
                "body": json.dumps({"message": "CORS preflight success"})
            }

        # Parse request body
        if event.get('httpMethod') == 'POST':
            body = json.loads(event['body'])
        else:
            body = event

        url = body.get('url')
        if not url:
            return {
                'statusCode': 400,
                'headers': cors_headers(),
                'body': json.dumps({
                    'error': 'URL is required',
                    'message': 'Please provide a valid news article URL'
                })
            }

        # Validate URL
        if not is_valid_url(url):
            return error_response('Invalid URL format', 400)

        # Generate cache key
        cache_key = generate_cache_key(url)

        # Check cache first
        cached_summary = get_cached_summary(cache_key)
        if cached_summary:
            logger.info(f"Returning cached summary for URL: {url}")
            return success_response(cached_summary, from_cache=True)

        # Fetch article content
        article_text = extract_article_content(url)
        if not article_text:
            return error_response('Failed to extract article content', 400)

        # Summarize using free API
        summary = summarize_text(article_text)
        if not summary:
            return error_response('Failed to generate summary', 500)

        # Prepare response data
        result = {
            'url': url,
            'title': extract_title_from_url(url),
            'summary': summary,
            'word_count': len(summary.split()),
            'original_length': len(article_text.split()),
            'compression_ratio': round(len(summary.split()) / len(article_text.split()), 2),
            'summarized_at': datetime.now().isoformat(),
            'cache_key': cache_key
        }

        # Cache the result
        cache_summary(cache_key, result)

        logger.info(f"Successfully summarized article from: {url}")
        return success_response(result)

    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return error_response(f'Internal server error: {str(e)}', 500)

def cors_headers():
    """Reusable CORS headers"""
    return {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type'
    }

def is_valid_url(url):
    """Validate URL format"""
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)', re.IGNORECASE)
    return url_pattern.match(url) is not None

def generate_cache_key(url):
    """Generate cache key from URL"""
    return hashlib.md5(url.encode()).hexdigest()

def get_cached_summary(cache_key):
    """Retrieve cached summary from DynamoDB"""
    try:
        response = cache_table.get_item(Key={'cache_key': cache_key})

        if 'Item' in response:
            item = response['Item']
            # Check if cache is still valid (24 hours)
            cached_time = datetime.fromisoformat(item['cached_at'])
            if datetime.now() - cached_time < timedelta(hours=24):
                return item['summary_data']

        return None

    except Exception as e:
        logger.warning(f"Error retrieving from cache: {str(e)}")
        return None

def cache_summary(cache_key, summary_data):
    """Cache summary in DynamoDB"""
    try:
        cache_table.put_item(
            Item={
                'cache_key': cache_key,
                'summary_data': summary_data,
                'cached_at': datetime.now().isoformat(),
                'ttl': int((datetime.now() + timedelta(days=7)).timestamp())  # Auto-delete after 7 days
            }
        )
    except Exception as e:
        logger.warning(f"Error caching summary: {str(e)}")

def extract_article_content(url):
    """Extract text content from news article"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header", "aside"]):
            script.decompose()

        # Try to find main content areas
        content_selectors = [
            'article',
            '.article-content',
            '.post-content',
            '.entry-content',
            '.content',
            'main',
            '#content'
        ]

        article_text = ""
        for selector in content_selectors:
            elements = soup.select(selector)
            if elements:
                for element in elements:
                    article_text += element.get_text(strip=True) + " "
                break

        # Fallback: get all paragraph text
        if not article_text.strip():
            paragraphs = soup.find_all('p')
            article_text = ' '.join([p.get_text(strip=True) for p in paragraphs])

        # Clean up text
        article_text = re.sub(r'\s+', ' ', article_text).strip()

        # Minimum content check
        if len(article_text) < 100:
            raise ValueError("Article content too short")

        return article_text[:5000]  # Limit to 5000 chars for API limits

    except Exception as e:
        logger.error(f"Error extracting article content: {str(e)}")
        return None

def extract_title_from_url(url):
    """Extract title from webpage"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.content, 'html.parser')

        title = soup.find('title')
        if title:
            return title.get_text().strip()

        # Fallback: try h1
        h1 = soup.find('h1')
        if h1:
            return h1.get_text().strip()

        return "Article"

    except:
        return "Article"

def summarize_text(text):
    """Summarize text using free NLP API"""
    try:
        # Using Hugging Face's free inference API
        api_url = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"

        headers = {
            "Authorization": "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",  # Replace with actual token
            "Content-Type": "application/json"
        }

        payload = {
            "inputs": text,
            "parameters": {
                "max_length": 150,
                "min_length": 50,
                "do_sample": False
            }
        }

        response = requests.post(api_url, headers=headers, json=payload, timeout=30)

        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                return result[0].get('summary_text', '')
            elif isinstance(result, dict):
                return result.get('summary_text', '')

        # Fallback: Simple extractive summarization
        return simple_extractive_summary(text)

    except Exception as e:
        logger.error(f"Error in summarization: {str(e)}")
        return simple_extractive_summary(text)

def simple_extractive_summary(text, max_sentences=3):
    """Simple extractive summarization fallback"""
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    if len(sentences) <= max_sentences:
        return '. '.join(sentences) + '.'

    # Score sentences by length and position
    scored_sentences = []
    for i, sentence in enumerate(sentences):
        score = len(sentence.split()) * (1 - i / len(sentences))  # Prefer longer, earlier sentences
        scored_sentences.append((score, sentence))

    # Select top sentences
    scored_sentences.sort(reverse=True)
    top_sentences = [s[1] for s in scored_sentences[:max_sentences]]

    return '. '.join(top_sentences) + '.'

def success_response(data, from_cache=False):
    """Generate success response"""
    return {
        'statusCode': 200,
        'headers': cors_headers(),
        'body': json.dumps({
            'success': True,
            'data': data,
            'from_cache': from_cache,
            'timestamp': datetime.now().isoformat()
        })
    }

def error_response(message, status_code=500):
    """Generate error response"""
    return {
        'statusCode': status_code,
        'headers': cors_headers(),
        'body': json.dumps({
            'success': False,
            'error': message,
            'timestamp': datetime.now().isoformat()
        })
    }

# For local testing
if __name__ == "__main__":
    # Test event
    test_event = {
        'httpMethod': 'POST',
        'body': json.dumps({
            'url': 'https://www.example.com/news-article'
        })
    }

    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
