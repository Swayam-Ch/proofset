"""On-demand Amazon Bedrock prices (USD per million tokens, us-east-1) for cost accounting."""

PRICES = {
    "amazon.nova-micro-v1:0": (0.035, 0.14),
    "amazon.nova-lite-v1:0": (0.06, 0.24),
    "anthropic.claude-haiku-4-5-20251001-v1:0": (1.0, 5.0),
}


def cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    key = model_id.split(".", 1)[1] if model_id.startswith(("us.", "eu.", "apac.", "global.")) else model_id
    price_in, price_out = PRICES.get(key, (0.0, 0.0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000
