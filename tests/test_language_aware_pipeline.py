import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "codes"))

from language_profiles import code_fence_for_file, get_language_profile  # noqa: E402
from prompt_builders import (  # noqa: E402
    build_analysis_messages,
    build_coding_messages,
    build_debugging_messages,
    build_planning_messages,
)


class LanguageAwarePipelineTests(unittest.TestCase):
    def test_c_planning_prompts_are_centric(self):
        profile = get_language_profile("c")
        planning_stages = build_planning_messages("paper-body", "JSON", profile)

        overall_plan_prompt = planning_stages[0][1]["content"]
        architecture_prompt = planning_stages[1][0]["content"]
        logic_prompt = planning_stages[2][0]["content"]

        self.assertIn("Before writing any C code", overall_plan_prompt)
        self.assertNotIn("Before writing any Python code", overall_plan_prompt)
        self.assertIn("ownership and lifetime", overall_plan_prompt.lower())
        self.assertIn("include/hashmap.h", architecture_prompt)
        self.assertIn("src/hashmap.c", architecture_prompt)
        self.assertIn("CMakeLists.txt", architecture_prompt)
        self.assertNotIn("ALWAYS write a main.py or app.py here", architecture_prompt)
        self.assertLess(
            logic_prompt.find("include/hashmap.h"),
            logic_prompt.find("src/hashmap.c"),
        )
        self.assertLess(
            logic_prompt.find("src/hashmap.c"),
            logic_prompt.find("tests/test_hashmap.c"),
        )

    def test_c_analysis_and_coding_prompts_cover_systems_concerns(self):
        profile = get_language_profile("c")
        context_lst = ["overall plan", "architecture", "logic task list"]
        config_yaml = "project:\n  target_language: c\n"

        analysis_messages = build_analysis_messages(
            paper_content="paper",
            paper_format="JSON",
            context_lst=context_lst,
            config_yaml=config_yaml,
            todo_file_name="include/hashmap.h",
            todo_file_desc="Public hashmap API",
            profile=profile,
        )
        analysis_text = "\n".join(message["content"] for message in analysis_messages)
        self.assertIn("public API", analysis_text)
        self.assertIn("ownership", analysis_text)
        self.assertIn("include guards", analysis_text)
        self.assertIn("thread-safety", analysis_text)

        coding_messages = build_coding_messages(
            paper_content="paper",
            paper_format="JSON",
            context_lst=context_lst,
            config_yaml=config_yaml,
            todo_file_name="include/hashmap.h",
            detailed_logic_analysis="Explain ownership and error codes.",
            done_file_lst=["config.yaml", "CMakeLists.txt"],
            done_file_dict={"CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)"},
            profile=profile,
        )
        coding_text = "\n".join(message["content"] for message in coding_messages)
        self.assertIn("```c", coding_text)
        self.assertIn("```cmake", coding_text)
        self.assertNotIn("```python\n## include/hashmap.h", coding_text)
        self.assertNotIn("Before writing any Python code", coding_text)

        cmake_messages = build_coding_messages(
            paper_content="paper",
            paper_format="JSON",
            context_lst=context_lst,
            config_yaml=config_yaml,
            todo_file_name="CMakeLists.txt",
            detailed_logic_analysis="Define executable and test targets.",
            done_file_lst=["config.yaml"],
            done_file_dict={},
            profile=profile,
        )
        cmake_text = "\n".join(message["content"] for message in cmake_messages)
        self.assertIn("```cmake", cmake_text)

    def test_c_debugging_prompt_and_verification_commands(self):
        profile = get_language_profile("c")

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\nproject(test C)\n",
                encoding="utf-8",
            )

            commands = profile.build_verification_commands(temp_dir)
            self.assertGreaterEqual(len(commands), 3)
            self.assertIn("cmake -S . -B build", commands[0])
            self.assertIn("cmake --build build", commands[1])
            self.assertIn("ctest --test-dir build", commands[2])

            debug_messages = build_debugging_messages(
                codes="```c\nint main(void) { return 0; }\n```",
                execution_error_msg="undefined reference to `hashmap_create`",
                profile=profile,
                verification_commands=commands,
            )
            debug_text = "\n".join(message["content"] for message in debug_messages)
            self.assertIn("compiler diagnostics", debug_text)
            self.assertIn("### Verification Commands", debug_text)
            self.assertIn("undefined reference", debug_text)

    def test_python_profile_still_uses_python_defaults(self):
        profile = get_language_profile("python")
        planning_stages = build_planning_messages("paper-body", "JSON", profile)
        overall_plan_prompt = planning_stages[0][1]["content"]
        architecture_prompt = planning_stages[1][0]["content"]

        self.assertIn("Before writing any Python code", overall_plan_prompt)
        self.assertIn("main.py", architecture_prompt)

        coding_messages = build_coding_messages(
            paper_content="paper",
            paper_format="JSON",
            context_lst=["plan", "design", "task"],
            config_yaml="project:\n  target_language: python\n",
            todo_file_name="main.py",
            detailed_logic_analysis="Entry point.",
            done_file_lst=["config.yaml"],
            done_file_dict={},
            profile=profile,
        )
        coding_text = "\n".join(message["content"] for message in coding_messages)
        self.assertIn("```python", coding_text)

    def test_code_fence_mapping_is_not_python_only(self):
        self.assertEqual(code_fence_for_file("include/hashmap.h"), "c")
        self.assertEqual(code_fence_for_file("src/hashmap.c"), "c")
        self.assertEqual(code_fence_for_file("CMakeLists.txt"), "cmake")
        self.assertEqual(code_fence_for_file("Makefile"), "makefile")
        self.assertEqual(code_fence_for_file("reproduce.sh"), "bash")
        self.assertEqual(code_fence_for_file("main.py"), "python")


if __name__ == "__main__":
    unittest.main()
