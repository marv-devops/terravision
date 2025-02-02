from ast import literal_eval
from contextlib import suppress
import click
import os
import re
from modules.tf_function_handlers import tf_function_handlers
from sys import exit
from pathlib import Path

# List of dictionary sections to output in log
output_sections = ["locals", "module", "resource", "data"]


def check_for_domain(string: str) -> bool:
    exts = [".com", ".net", ".org", ".io", ".biz"]
    for dot in exts:
        if dot in string:
            return True
    return False


def url(string: str) -> str:
    if string.count("://") == 0:
        return "https://" + string
    return string


def check_for_tf_functions(string):
    for tf_function in dir(tf_function_handlers):
        if tf_function + "(" in string and "ERROR!_" + tf_function not in string:
            return tf_function
    return False


def find_nth(string, substring, n):
    if n == 1:
        return string.find(substring)
    else:
        return string.find(substring, find_nth(string, substring, n - 1) + 1)


def unique_services(nodelist: list) -> list:
    service_list = []
    for item in nodelist:
        service = str(item.split(".")[0]).strip()
        service_list.append(service)
    return sorted(set(service_list))


def find_between(text, begin, end, alternative="", replace=False, occurrence=1):
    if not text:
        return
    # Handle Nested Functions with multiple brackets in parameters
    if begin not in text and not replace:
        return ""
    elif begin not in text and replace:
        return text
    if end == ")":
        begin_index = text.find(begin)
        # begin_index = find_nth(text, begin, occurrence)
        end_index = find_nth(text, ")", occurrence)
        end_index = text.find(")", begin_index)
        middle = text[begin_index + len(begin) : end_index]
        num_brackets = middle.count("(")
        if num_brackets >= 1:
            end_index = find_nth(text, ")", num_brackets + 1)
            middle = text[begin_index + len(begin) : end_index]
        return middle
    else:
        middle = text.split(begin, 1)[1].split(end, 1)[0]
    # If looking for a space but no space found, terminate with any non alphanumeric char except _
    # so that variable names don't get broken up (useful for extracting variable names and locals)
    if (end == " " or end == "") and not middle.endswith(" "):
        for i in range(0, len(middle)):
            char = middle[i]
            if not char.isalpha() and char != "_" and char != "-":
                end = char
                middle = text.split(begin, 1)[1].split(end, 1)[0]
                break
    if replace:
        return text.replace(begin + middle, alternative, 1)
    else:
        return middle


def pretty_name(name: str, show_title=True) -> str:
    """
    Beautification for AWS Labels
    """
    resourcename = ""
    if "null_" in name or "random" in name or "time_sleep" in name:
        return "Null"
    else:
        name = name.replace("aws_", "")
    servicename = name.split(".")[0]
    service_label = name.split(".")[-1]
    if servicename == "route_table_association":
        servicename = "Route Table"
    if servicename == "ecs_service_fargate":
        servicename = "Fargate"
    if servicename == "instance":
        servicename = "ec2"
    if servicename == "lambda_function":
        servicename = ""
    if servicename == "iam_role":
        servicename = "role"
    if servicename == "dx":
        servicename = "Direct Connect"
    if servicename == "iam_policy":
        servicename = "policy"
    if resourcename == "this":
        resourcename = ""
    if servicename[0:3] in [
        "acm",
        "ec2",
        "kms",
        "elb",
        "nlb",
        "efs",
        "ebs",
        "iam",
        "api",
        "acm",
        "ecs",
        "rds",
        "lb",
        "alb",
        "elb",
        "nlb",
        "nat",
    ]:
        acronym = servicename[0:3]
        servicename = servicename.replace(acronym, acronym.upper())
        servicename = servicename[0:3] + " " + servicename[4:].title()
    else:
        servicename = servicename.title()
    final_label = (service_label.title() if show_title else "") + " " + servicename
    final_label = final_label[:22]
    final_label = final_label.replace("_", " ")
    final_label = final_label.replace("-", " ")
    final_label = final_label.replace("This", "").strip()
    return final_label


def replace_variables(vartext, filename, all_variables, quotes=False):
    # Replace Variables found within resource meta data
    if isinstance(filename, list):
        filename = filename[0]
    vartext = str(vartext).strip()
    replaced_vartext = vartext
    var_found_list = re.findall("var\.[A-Za-z0-9_-]+", vartext)
    if var_found_list:
        for varstring in var_found_list:
            varname = varstring.replace("var.", "").lower()
            with suppress(Exception):
                if str(all_variables[varname]) == "":
                    replaced_vartext = replaced_vartext.replace(varstring, '""')
                else:
                    replacement_value = getvar(varname, all_variables)
                    if replacement_value == "NOTFOUND":
                        click.echo(
                            click.style(
                                f"\nERROR: No variable value supplied for var.{varname} in {os.path.basename(os.path.dirname(filename))}/{os.path.basename(filename)}",
                                fg="red",
                                bold=True,
                            )
                        )
                        click.echo(
                            "Consider passing a valid Terraform .tfvars variable file with the --varfile parameter or setting a TF_VAR env variable\n"
                        )
                        exit()
                    replaced_vartext = replaced_vartext.replace(
                        "${" + varstring + "}", str(replacement_value)
                    )
                    replaced_vartext = replaced_vartext.replace(
                        varstring, str(replacement_value)
                    )
        return replaced_vartext


def output_log(tfdata):
    for section in output_sections:
        click.echo(f"\n  {section.title()} list :")
        if tfdata.get("all_" + section):
            for file, valuelist in tfdata["all_" + section].items():
                filepath = Path(file)
                fname = filepath.parent.name + "/" + filepath.name
                for item in valuelist:
                    if isinstance(item, dict):
                        for key in item:
                            click.echo(f"    {fname}: {key}.{next(iter(item[key]))}")
                    else:
                        click.echo(f"    {fname}: {item}")
    if tfdata.get("variable_map"):
        click.echo("\n  Variable List:")
        for module, variable in tfdata["variable_map"].items():
            if module == "main":
                variable["source"] = "main"
            click.echo(f"\n    Module: {module}")
            for key in variable:
                if not key.startswith("source"):
                    click.echo(f"      var.{key} = {variable[key]}")
    return


def getvar(variable_name, all_variables_dict):
    # See if variable exists as an environment variable
    env_var = os.getenv("TF_VAR_" + variable_name)
    if env_var:
        return env_var
    # Check if it exists in all variables dict
    if variable_name in all_variables_dict:
        return all_variables_dict[variable_name]
    else:
        # Check if same variable with different casing exists
        for var in all_variables_dict:
            if var.lower() == variable_name.lower():
                return all_variables_dict[var]
        return "NOTFOUND"


def find_resource_references(searchdict: dict, target_resource: str) -> dict:
    final_dict = dict()
    for item in searchdict:
        if target_resource in searchdict[item]:
            final_dict[item] = searchdict[item]
    return final_dict


def list_of_parents(searchdict: dict, target: str):
    final_list = list()
    for key, value in searchdict.items():
        if isinstance(value, str):
            if target in value:
                final_list.append(key)
        elif isinstance(value, dict):
            for subkey in value:
                if target in value[subkey]:
                    final_list.append(key)
    return final_list


def list_of_dictkeys_containing(searchdict: dict, target_keyword: str) -> list:
    final_list = list()
    for item in searchdict:
        if target_keyword in item:
            final_list.append(item)
    return final_list


# Cleanup lists with special characters
def fix_lists(eval_string: str):
    eval_string = eval_string.replace("${[]}", "[]")
    if "${" in eval_string:
        eval_string = "".join(eval_string.rsplit("}", 1))
        eval_string = eval_string.replace("${", "", 1)
    eval_string = eval_string.replace("[\"['", "")
    eval_string = eval_string.replace("']\"]", "")
    # eval_string = eval_string.replace("['", '')
    # eval_string = eval_string.replace("']", '')
    eval_string = eval_string.replace('["[', "[")
    eval_string = eval_string.replace(']"]', "]")
    eval_string = eval_string.replace("[[", "[")
    eval_string = eval_string.replace(",)", ")")
    eval_string = eval_string.replace(",]", "]")
    eval_string = eval_string.replace("]]", "]")
    eval_string = eval_string.replace("[True]", "True")
    eval_string = eval_string.replace("[False]", "False")
    return eval_string


# Cleans out special characters
def cleanup_curlies(text: str) -> str:
    text = str(text)
    for ch in ["$", "{", "}"]:
        if ch in text:
            text = text.replace(ch, " ")
    return text.strip()


# Cleans out special characters
def cleanup(text: str) -> str:
    text = str(text)
    # for ch in ['\\', '`', '*', '{', '}', '[', ']', '(', ')', '>', '!', '$', '\'', '"']:
    for ch in ["\\", "`", "*", "{", "}", "(", ")", ">", "!", "$", "'", '"', "  "]:
        if ch in text:
            text = text.replace(ch, " ")
    return text.strip()
