"""Sample runner: screen one name through the sanctions-screening subagent."""

from sanctions_screening import screen_name

SAMPLE_NAME = "Dmitri Kovalenko"

if __name__ == "__main__":
    result = screen_name(SAMPLE_NAME)
    print(result)
