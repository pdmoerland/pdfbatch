"""APA author formatting and sentence-case titles."""

from typing import List

def format_authors_apa(authors: List[str]) -> str:
    """
    Format author list according to APA 7th Edition.
    
    Rules:
        - 1 author: "Smith, J."
        - 2 authors: "Smith, J., & Doe, A."
        - 3-20 authors: "Smith, J., Doe, A., & Jones, B."
        - 21+ authors: First 19 authors, ..., last author
    
    Args:
        authors: List of author names
        
    Returns:
        APA formatted author string
    """
    if not authors:
        return ""
    
    # Convert names to APA format (Last, F. M.)
    formatted = [format_single_author(name) for name in authors]
    
    if len(formatted) == 1:
        return formatted[0]
    
    elif len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    
    elif len(formatted) <= 20:
        # All but last, comma separated, & before last
        all_but_last = ", ".join(formatted[:-1])
        return f"{all_but_last}, & {formatted[-1]}"
    
    else:
        # 21+ authors: first 19, ..., last
        first_19 = ", ".join(formatted[:19])
        return f"{first_19}, ... {formatted[-1]}"


def format_single_author(name: str) -> str:
    """
    Format a single author name to APA style.
    
    Converts:
        "John Smith" -> "Smith, J."
        "John A. Smith" -> "Smith, J. A."
        "Smith, John" -> "Smith, J."
        
    Args:
        name: Author name in any format
        
    Returns:
        APA formatted name
    """
    name = name.strip()
    
    # Already in "Last, First" format?
    if ',' in name:
        parts = name.split(',', 1)
        last = parts[0].strip()
        first_parts = parts[1].strip().split()
        initials = ' '.join(f"{p[0]}." for p in first_parts if p)
        return f"{last}, {initials}" if initials else last
    
    # "First Last" or "First Middle Last" format
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    
    last = parts[-1]
    first_parts = parts[:-1]
    initials = ' '.join(f"{p[0]}." for p in first_parts if p)
    
    return f"{last}, {initials}" if initials else last


def to_sentence_case(title: str) -> str:
    """
    Convert title to sentence case for APA.
    
    APA uses sentence case for article titles:
    "Attention Is All You Need" -> "Attention is all you need"
    
    Preserves:
        - First letter capitalized
        - Acronyms (all caps, 2+ letters)
        - Proper nouns (heuristic: words with internal capitals)
        
    Args:
        title: Original title
        
    Returns:
        Sentence case title
    """
    if not title:
        return ""
    
    words = title.split()
    result = []
    
    for i, word in enumerate(words):
        # Keep first word capitalized
        if i == 0:
            result.append(word)
        # Keep acronyms (all uppercase, 2+ letters)
        elif word.isupper() and len(word) > 1:
            result.append(word)
        # Keep words with internal capitals (like "iPhone", "GPT-4")
        elif any(c.isupper() for c in word[1:]):
            result.append(word)
        # Lowercase everything else
        else:
            result.append(word.lower())
    
    return ' '.join(result)


