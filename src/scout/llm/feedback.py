"""The prompt for the next round after Python found problems in the last answer."""
from pydantic import BaseModel


def retry_prompt(base: str, last: BaseModel | None, errors: list[str]) -> str:
    if last is None or not errors:
        return base
    return (f"{base}\n\nYour last answer:\n{last.model_dump_json()}\n\n"
            "Python compared it with the records and found these problems. Each problem gives the value "
            "in the record. Keep the parts that are correct, and fix only these:\n"
            + "\n".join(f"- {e}" for e in errors))
