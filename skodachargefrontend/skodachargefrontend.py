def root():
    if not sessions:
        html_content = render_template('index.html')  # Renamed variable
        return html_content  # Updated to use html_content
    #... other code ...