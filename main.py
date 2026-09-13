import argparse


def main():
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. — agente de voz")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="Executar somente no terminal")
    mode.add_argument("--demo", action="store_true", help="Abrir a demonstração visual")
    args = parser.parse_args()
    if args.headless:
        from wakeword.detect_microphone_service import start
        start()
        return 0
    from speakbar.__main__ import main as start_interface
    return start_interface(["--demo"] if args.demo else [])


if __name__ == "__main__":
    raise SystemExit(main())
