def strip_two_spaces(text: str) -> str:
    """Remove exactly two leading spaces from each line."""
    return "\n".join(
        line[2:] if line.startswith("  ") else line
        for line in text.split("\n")
    )

if __name__ == "__main__":
    sample_text = '''
  >> articleUrl
  getHtml
  xpath=>//ul[@class='xwfb_listbox']/li//a/@href
  links
  all

  -- title
  getHtml
  xpath=>//ul[@class='xwfb_listbox']/li//a/@title
  all
'''
    print(strip_two_spaces(sample_text))