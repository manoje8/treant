from main import setup_argument_parser


def test_setup_argument_parser():
    parser = setup_argument_parser()
    args = parser.parse_args(
        ["test.pdf", "--parse-method", "google_doc_ai", "--stats", "-o", "out.txt"]
    )

    assert args.input_file == "test.pdf"
    assert args.parse_method == "google_doc_ai"
    assert args.stats is True
    assert args.output == "out.txt"
