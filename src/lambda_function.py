"""
AWS Lambda function to scrape, summarize, and cache news articles.

This function acts as a serverless backend for a news summarizer application.
It exposes a single POST endpoint that accepts a URL, scrapes the article content,
generates a summary using a third-party API, and caches the result in DynamoDB
to reduce latency and redundant processing for subsequent requests.
"""

import json
import boto3
import requests
from datetime import datetime, timedelta
import hashlib
import re
from bs4 import BeautifulSoup
import logging

# --- GLOBAL INITIALIZATION ---

# Configure logging for CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize the DynamoDB client. Boto3 will use the Lambda's IAM role for credentials.
dynamodb = boto3.resource('dynamodb')
cache_table = dynamodb.Table('news-summarizer-cache') # Replace with your table name if different

def lambda_handler(event, context):
    """
    Main AWS Lambda handler function.

    This function orchestrates the entire process:
    1. Handles CORS and validates the incoming request.
    2. Checks a DynamoDB table for a cached summary.
    3. If not cached, scrapes the article content from the URL.
    4. Calls an external API to summarize the text.
    5. Caches the new result in DynamoDB.
    6. Returns a formatted JSON response.

    Args:
        event (dict): The event dictionary containing request details (e.g., body, headers).
        context (object): The context object providing runtime information.

    Returns:
        dict: A formatted API Gateway response dictionary.
    """
    try:
        # --- 1. CORS Preflight and Request Validation ---

        # Handle CORS preflight requests sent by browsers to check permissions.
        if event.get("httpMethod") == "OPTIONS":
            return {
                "statusCode": 200,
                "headers": cors_headers(),
                "body": json.dumps({"message": "CORS preflight success"})
            }

        # Parse the request body, handling both API Gateway proxy events and direct invocations.
        if event.get('httpMethod') == 'POST':
            body = json.loads(event['body'])
        else:
            body = event

        # Ensure a URL was provided in the request body.
        url = body.get('url')
        if not url:
            return error_response('URL is required', 400)

        # Validate the format of the provided URL.
        if not is_valid_url(url):
            return error_response('Invalid URL format', 400)

        # --- 2. Caching Logic ---

        # Generate a unique and consistent cache key from the URL using an MD5 hash.
        cache_key = generate_cache_key(url)

        # Check DynamoDB for a fresh, valid summary.
        cached_summary = get_cached_summary(cache_key)
        if cached_summary:
            logger.info(f"Returning cached summary for URL: {url}")
            return success_response(cached_summary, from_cache=True)

        # --- 3. Core Processing (if not cached) ---

        # Scrape the main textual content from the news article's webpage.
        logger.info(f"Fetching fresh content for URL: {url}")
        article_text, article_title = extract_article_content_and_title(url)
        if not article_text:
            return error_response('Failed to extract article content', 400)

        # Send the extracted text to a summarization API.
        summary = summarize_text(article_text)
        if not summary:
            return error_response('Failed to generate summary', 500)

        # --- 4. Response Preparation and Caching ---

        # Prepare the final data structure for the response body.
        original_word_count = len(article_text.split())
        summary_word_count = len(summary.split())
        
        result = {
            'url': url,
            'title': article_title,
            'summary': summary,
            'word_count': summary_word_count,
            'original_length': original_word_count,
            # Calculate the compression ratio, handling potential division by zero.
            'compression_ratio': round(original_word_count / summary_word_count, 2) if summary_word_count > 0 else 0,
            'summarized_at': datetime.now().isoformat()
        }

        # Store the newly generated summary in DynamoDB for future requests.
        cache_summary(cache_key, result)

        logger.info(f"Successfully summarized and cached article from: {url}")
        return success_response(result)

    except Exception as e:
        # Catch-all for any unexpected errors during execution.
        logger.error(f"Error in lambda_handler: {str(e)}")
        return error_response(f'Internal server error: {str(e)}', 500)

# --- Helper Functions ---

def cors_headers():
    """Provides a reusable dictionary of CORS headers for API Gateway responses."""
    return {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*', # Allow requests from any origin
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type'
    }

def is_valid_url(url):
    """Validates the URL format using a regular expression."""
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z09](?:[A-Z09-]{0,61}[A-Z09])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)', re.IGNORECASE)
    return url_pattern.match(url) is not None

def generate_cache_key(url):
    """Generates a consistent MD5 hash of a URL to use as a DynamoDB primary key."""
    return hashlib.md5(url.encode()).hexdigest()

def get_cached_summary(cache_key):
    """
    Retrieves a cached summary from DynamoDB if it exists and is less than 24 hours old.
    
    Args:
        cache_key (str): The MD5 hash of the URL.
        
    Returns:
        dict or None: The cached summary data, or None if not found or expired.
    """
    try:
        response = cache_table.get_item(Key={'cache_key': cache_key})

        if 'Item' in response:
            item = response['Item']
            # Check if the cached item is still valid (e.g., within 24 hours).
            cached_time = datetime.fromisoformat(item['cached_at'])
            if datetime.now() - cached_time < timedelta(hours=24):
                return item['summary_data']
            else:
                logger.info(f"Cache expired for key: {cache_key}")

        return None
    except Exception as e:
        logger.warning(f"Error retrieving from cache: {str(e)}")
        return None

def cache_summary(cache_key, summary_data):
    """
    Stores a summary result in DynamoDB with a Time-To-Live (TTL) attribute.
    
    Args:
        cache_key (str): The MD5 hash of the URL.
        summary_data (dict): The summary data to cache.
    """
    try:
        # DynamoDB's TTL feature automatically deletes items after the specified timestamp.
        ttl_timestamp = int((datetime.now() + timedelta(days=7)).timestamp())
        
        cache_table.put_item(
            Item={
                'cache_key': cache_key,
                'summary_data': summary_data,
                'cached_at': datetime.now().isoformat(),
                'ttl': ttl_timestamp  # Auto-delete after 7 days
            }
        )
    except Exception as e:
        logger.warning(f"Error caching summary: {str(e)}")

def extract_article_content_and_title(url):
    """
    Extracts the main text content and title from a news article's HTML.
    
    Args:
        url (str): The URL of the news article.
        
    Returns:
        tuple: A tuple containing the article text (str) and title (str), or (None, None) on failure.
    """
    try:
        # Use a common User-Agent to mimic a browser and avoid being blocked.
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        soup = BeautifulSoup(response.content, 'html.parser')

        # --- Title Extraction ---
        title_tag = soup.find('title')
        title = title_tag.get_text().strip() if title_tag else "Article"

        # --- Content Extraction ---
        # Remove common non-content tags to reduce noise.
        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.decompose()

        # Try to find the main content by looking for common semantic tags and class names.
        content_selectors = ['article', '.article-body', '.story-content', 'main', '#content']
        text_parts = []
        for selector in content_selectors:
            elements = soup.select(selector)
            if elements:
                for element in elements:
                    text_parts.append(element.get_text(separator=' ', strip=True))
                break # Stop after the first successful selector.

        # Fallback: If no specific content area is found, get all paragraph text.
        if not text_parts:
            paragraphs = soup.find_all('p')
            text_parts = [p.get_text(strip=True) for p in paragraphs]

        article_text = ' '.join(text_parts)
        # Clean up extra whitespace.
        article_text = re.sub(r'\s+', ' ', article_text).strip()

        # Ensure the content is substantial enough to be summarized.
        if len(article_text.split()) < 50:
            return None, None

        # Truncate the text to stay within the limits of the summarization API.
        return article_text[:5000], title

    except Exception as e:
        logger.error(f"Error extracting article content: {str(e)}")
        return None, None

def summarize_text(text):
    """
    Summarizes the provided text using the Hugging Face Inference API.
    Includes a simple fallback summarizer if the API call fails.
    
    Args:
        text (str): The text to be summarized.
        
    Returns:
        str: The summarized text.
    """
    try:
        # !!! SECURITY WARNING !!!
        # Hardcoding API keys is a major security risk.
        # This token should be stored in AWS Secrets Manager or as an encrypted Lambda environment variable.
        api_key = os.environ.get("HUGGING_FACE_API_KEY", "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") # Example fallback
        
        api_url = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "inputs": text,
            "parameters": {"max_length": 150, "min_length": 40, "do_sample": False}
        }
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=30)

        if response.status_code == 200:
            result = response.json()
            # The API can return a list or a dictionary, so we handle both cases.
            if isinstance(result, list) and len(result) > 0:
                return result[0].get('summary_text', '')
        
        # If the API call fails, log the issue and use the simple fallback.
        logger.warning(f"Hugging Face API failed with status {response.status_code}. Using fallback summarizer.")
        return simple_extractive_summary(text)

    except Exception as e:
        logger.error(f"Error in summarization API call: {str(e)}")
        # If any exception occurs (e.g., timeout), use the simple fallback.
        return simple_extractive_summary(text)

def simple_extractive_summary(text, num_sentences=3):
    """A very basic fallback summarizer that picks the most important sentences."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    # Filter out very short or non-sensical "sentences".
    sentences = [s.strip() for s in sentences if len(s.split()) > 5]

    if len(sentences) <= num_sentences:
        return ' '.join(sentences)

    # A simple scoring mechanism: prefer longer sentences that appear earlier in the article.
    scored_sentences = []
    for i, sentence in enumerate(sentences):
        score = len(sentence.split()) * (1 - (i / len(sentences)))
        scored_sentences.append((score, sentence))

    # Get the top N scored sentences and sort them by their original appearance.
    top_sentences = sorted(scored_sentences, key=lambda x: x[0], reverse=True)[:num_sentences]
    top_sentences.sort(key=lambda x: sentences.index(x[1])) # Re-sort by original order
    
    return ' '.join([s[1] for s in top_sentences])

def success_response(data, from_cache=False):
    """Generates a consistent, successful API Gateway response."""
    return {
        'statusCode': 200,
        'headers': cors_headers(),
        'body': json.dumps({
            'success': True,
            'data': data,
            'from_cache': from_cache
        })
    }

def error_response(message, status_code=500):
    """Generates a consistent, failed API Gateway response."""
    return {
        'statusCode': status_code,
        'headers': cors_headers(),
        'body': json.dumps({
            'success': False,
            'error': message
        })
    }

# This block is for local testing only and will not be executed in the AWS Lambda environment.
if __name__ == "__main__":
    # Simulate an API Gateway POST event.
    test_event = {
        'httpMethod': 'POST',
        'body': json.dumps({
            'url': 'https://www.bbc.com/news/technology-58289753' # Example URL
        })
    }
    # Call the handler function directly.
    result = lambda_handler(test_event, None)
    # Pretty-print the JSON result.
    print(json.dumps(json.loads(result['body']), indent=2))
