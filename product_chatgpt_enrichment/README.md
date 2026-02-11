# ChatGPT Product Enrichment for Odoo 18

Automatically enrich your product information using OpenAI's ChatGPT API.

## Features

- 🤖 **Automatic Enrichment**: Products are automatically enhanced when created
- ✍️ **Manual Enrichment**: Enrich existing products with a single click
- 🎨 **Customizable Prompts**: Configure your own prompt templates
- 🔧 **Flexible Configuration**: Choose between GPT-4, GPT-4 Turbo, or GPT-3.5 Turbo
- 📊 **Tracking**: See when products were last enriched
- 🔒 **Secure**: API keys are stored securely

## Installation

1. Install the module from Apps menu
2. Go to **Settings > Technical > ChatGPT Configuration**
3. Create a new configuration:
   - Enter your OpenAI API key (get one at https://platform.openai.com/api-keys)
   - Select your preferred GPT model
   - Configure auto-enrichment settings
   - Customize the prompt template if needed
4. Click **Test Connection** to verify your API key works

## Usage

### Automatic Enrichment

When auto-enrichment is enabled, new products will automatically get enhanced descriptions when created.

### Manual Enrichment

1. Open any product
2. Click the **Enrich with ChatGPT** button in the header
3. The product description will be automatically updated with AI-generated content
4. View the enrichment details in the **AI Enrichment** tab

### Configuration

Access configuration at: **Settings > Technical > ChatGPT Configuration > Settings**

- **API Key**: Your OpenAI API key
- **Model**: Choose between GPT-4, GPT-4 Turbo, or GPT-3.5 Turbo
- **Auto-Enrich**: Enable/disable automatic enrichment for new products
- **Max Tokens**: Maximum length of generated content
- **Temperature**: Controls creativity (0 = focused, 1 = creative)
- **Prompt Template**: Customize the prompt sent to ChatGPT

## Requirements

- Odoo 18.0
- OpenAI API key
- Internet connection

## Support

For issues or questions, please visit: https://github.com/pixeeplay/nus-odoo18

## License

LGPL-3

## Author

Pixeeplay
