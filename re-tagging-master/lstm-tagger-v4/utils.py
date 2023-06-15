import os
import re
import codecs
import numpy as np
import theano


models_path = "./models"

if os.getenv("CONLLEVAL") is None:
    eval_path = "./evaluation"
else:
    eval_path = os.getenv("CONLLEVAL")

if os.getenv("CONLLEVAL_TEMP") is None:
    eval_temp = os.path.join(eval_path, "temp")
else:
    eval_temp = os.getenv("CONLLEVAL_TEMP")

eval_script = os.path.join(eval_path, "conlleval")


def get_name(parameters):
    """
    Generate a model name from its parameters.
    """
    l = []
    for k, v in parameters.items():
        if type(v) is str and "/" in v:
            l.append((k, v[::-1][:v[::-1].index('/')][::-1]))
        else:
            l.append((k, v))
    name = ",".join(["%s=%s" % (k, str(v).replace(',', '')) for k, v in l])
    return "".join(i for i in name if i not in "\/:*?<>|")

def get_path(parameters):
    """
    Generate a model name from its parameters.
    """
    l = []
    # selected_keys = {'tag_scheme', 'word_dim', 'word_bidirect', 'pre_emb', 'crf', 'all_emb', 'external_features', 'dropout', 'prefix', 'lr_method'}
    selected_keys = {'prefix'}
    for k, v in parameters.items():
        if k in selected_keys:
            if type(v) is str and "/" in v:
                l.append((k, v[::-1][:v[::-1].index('/')][::-1]))
            else:
                l.append((k, v))
    name = ",".join(["%s=%s" % (k, str(v).replace(',', '')) for k, v in l])
    return "".join(i for i in name if i not in "\/:*?<>|")

def set_values(name, param, pretrained):
    """
    Initialize a network parameter with pretrained values.
    We check that sizes are compatible.
    """
    param_value = param.get_value()
    if pretrained.size != param_value.size:
        raise Exception(
            "Size mismatch for parameter %s. Expected %i, found %i."
            % (name, param_value.size, pretrained.size)
        )
    param.set_value(np.reshape(
        pretrained, param_value.shape
    ).astype(np.float32))


def shared(shape, name):
    """
    Create a shared object of a numpy array.
    """

    if len(shape) == 1:
        value = np.zeros(shape)  # bias are initialized with zeros
    else:
        drange = np.sqrt(6. / (np.sum(shape)))
        value = drange * np.random.uniform(low=-1.0, high=1.0, size=shape)
    return theano.shared(value=value.astype(theano.config.floatX), name=name)


def create_dico(item_list):
    """
    Create a dictionary of items from a list of list of items.
    """
    assert type(item_list) is list
    dico = {}
    for items in item_list:
        for item in items:
            if item not in dico:
                dico[item] = 1
            else:
                dico[item] += 1
    return dico


def create_mapping(dico):
    """
    Create a mapping (item to ID / ID to item) from a dictionary.
    Items are ordered by decreasing frequency.
    """
    sorted_items = sorted(dico.items(), key=lambda x: (-x[1], x[0]))
    id_to_item = {i: v[0] for i, v in enumerate(sorted_items)}
    item_to_id = {v: k for k, v in id_to_item.items()}
    return item_to_id, id_to_item


def zero_digits(s):
    """
    Replace every digit in a string by a zero.
    """
    return re.sub('\d', '0', s)


def iob2(tags):
    """
    Check that tags have a valid IOB format.
    Tags in IOB1 format are converted to IOB2.
    """

    # tagset = {tag for i, tag in enumerate(tags)}
    # print tagset
    for i, tag in enumerate(tags):
        if tag == 'O':
            continue
        split = tag.split('-')
        if len(split) != 2 or split[0] not in ['I', 'B']:
            return False
        if split[0] == 'B':
            continue
        elif i == 0 or tags[i - 1] == 'O':  # conversion IOB1 to IOB2
            tags[i] = 'B' + tag[1:]
        elif tags[i - 1][1:] == tag[1:]:
            continue
        else:  # conversion IOB1 to IOB2
            tags[i] = 'B' + tag[1:]
    return True


def iob_iobes(tags):
    """
    IOB -> IOBES
    """
    new_tags = []
    for i, tag in enumerate(tags):
        if tag == 'O':
            new_tags.append(tag)
        elif tag.split('-')[0] == 'B':
            if i + 1 != len(tags) and \
               tags[i + 1].split('-')[0] == 'I':
                new_tags.append(tag)
            else:
                new_tags.append(tag.replace('B-', 'S-'))
        elif tag.split('-')[0] == 'I':
            if i + 1 < len(tags) and \
                    tags[i + 1].split('-')[0] == 'I':
                new_tags.append(tag)
            else:
                new_tags.append(tag.replace('I-', 'E-'))
        else:
            raise Exception('Invalid IOB format!')
    return new_tags


def iobes_iob(tags):
    """
    IOBES -> IOB
    """
    new_tags = []
    for i, tag in enumerate(tags):
        if tag.split('-')[0] == 'B':
            new_tags.append(tag)
        elif tag.split('-')[0] == 'I':
            new_tags.append(tag)
        elif tag.split('-')[0] == 'S':
            new_tags.append(tag.replace('S-', 'B-'))
        elif tag.split('-')[0] == 'E':
            new_tags.append(tag.replace('E-', 'I-'))
        elif tag.split('-')[0] == 'O':
            new_tags.append(tag)
        else:
            raise Exception('Invalid format!')
    return new_tags


def insert_singletons(words, singletons, p=0.5):
    """
    Replace singletons by the unknown word with a probability p.
    """
    # print singletons
    new_words = []
    for word in words:
        if word in singletons and np.random.uniform() < p:
            new_words.append(0)
        else:
            new_words.append(word)
    return new_words


def pad_word_chars(words):
    """
    Pad the characters of the words in a sentence.
    Input:
        - list of lists of ints (list of words, a word being a list of char indexes)
    Output:
        - padded list of lists of ints
        - padded list of lists of ints (where chars are reversed)
        - list of ints corresponding to the index of the last character of each word
    """
    max_length = max([len(word) for word in words])
    char_for = []
    char_rev = []
    char_pos = []
    for word in words:
        padding = [0] * (max_length - len(word))
        char_for.append(word + padding)
        char_rev.append(word[::-1] + padding)
        char_pos.append(len(word) - 1)
    return char_for, char_rev, char_pos


def create_input2(data, parameters, add_label, singletons=None):
    """
    Take sentence data and return an input for
    the training or the evaluation function.
    """
    features = []
    if parameters['external_features'] != "None":
        features = [{'name': y[0], 'column': int(y[1]), 'dim': int(y[2])} for y in
                    [x.split('.') for x in parameters['external_features'].split(",")]]

    words = data['words']

    # print words
    # assert 0
    chars = data['chars']
    if singletons is not None:
        words = insert_singletons(words, singletons)

    if parameters['cap_dim']:
        caps = data['caps']

    char_for, char_rev, char_pos = pad_word_chars(chars)
    input = []

    if parameters['word_dim']:
        input.append(words)

    for f in features:
        input.append(data[f['name']])

    if parameters['char_dim']:
        input.append(char_for)
        if parameters['char_bidirect']:
            input.append(char_rev)
        input.append(char_pos)

    if parameters['cap_dim']:
        input.append(caps)

    if add_label:
        input.append(data['tags'])

    return input

def evaluate(parameters, f_eval, raw_sentences, parsed_sentences, id_to_tag,
             blog=False, eval_script=eval_script, remove_temp=False
             ):
    """
    Evaluate current model using CoNLL script.
    """
    n_tags = len(id_to_tag)
    predictions = []
    count = np.zeros((n_tags, n_tags), dtype=np.int32)

    log = []
    for raw_sentence, data in zip(raw_sentences, parsed_sentences):
        if (blog):
            log.append("")
            log.append("")
            log.append("SENTENCE+\t" + ' '.join(tokens[0] for tokens in raw_sentence))

        input = create_input2(data, parameters, False)
        if parameters['crf']:
            y_preds = np.array(f_eval(*input))[1:-1]
        else:
            y_preds = f_eval(*input).argmax(axis=1)
        y_reals = np.array(data['tags']).astype(np.int32)
        assert len(y_preds) == len(y_reals)
        p_tags = [id_to_tag[y_pred] for y_pred in y_preds]
        r_tags = [id_to_tag[y_real] for y_real in y_reals]
        if parameters['tag_scheme'] == 'iobes':
            p_tags = iobes_iob(p_tags)
            r_tags = iobes_iob(r_tags)


        for i, (y_pred, y_real) in enumerate(zip(y_preds, y_reals)):
            new_line = " ".join(raw_sentence[i][:-1] + [r_tags[i], p_tags[i]])
            predictions.append(new_line)
            count[y_real, y_pred] += 1
            if blog:
                if (r_tags[i] != p_tags[i]):
                    log.append( "FALSE\t" + str(i) + "\t" + raw_sentence[i][0] + "\t" + r_tags[i] + "\t" + p_tags[i])

        if blog:
            for i in range(len(y_preds)):
                if (r_tags[i] == p_tags[i] and p_tags[i] != "O"):
                    log.append("TRUE\t" + str(i) + "\t" + raw_sentence[i][0] + "\t" + r_tags[i] + "\t" + p_tags[i])

        predictions.append("")



    # from pprint import pprint
    # pprint (predictions)
    # Write predictions to disk and run CoNLL script externally
    eval_id = np.random.randint(1000000, 2000000)
    from datetime import datetime
    output_path = os.path.join(eval_temp, "eval.%i.%i.output" % (datetime.now().microsecond, eval_id))

    scores_path = os.path.join(eval_temp, "eval.%i.%i.scores" % (datetime.now().microsecond, eval_id))
    with codecs.open(output_path, 'w', 'utf8') as f:
        f.write("\n".join(predictions))
    os.system("%s < %s > %s" % (eval_script, output_path, scores_path))

    # CoNLL evaluation results
    print scores_path
    eval_lines = [l.rstrip() for l in codecs.open(scores_path, 'r', 'utf8')]
    eval_lines.append(output_path)
    for line in eval_lines:
        print line

    # Remove temp files
    if remove_temp:
        os.remove(output_path)
        os.remove(scores_path)


    sResult = ("{}\t{}\t{}\t%s{}\t{}\t{}\t{}\t{}\t\n" % ("{}\t" * n_tags)).format(
        "ID", "NE", "Total",
        *([id_to_tag[i] for i in xrange(n_tags)] + ["Predict"] + ["Correct"] + ["Recall"] + ["Precision"]+ ["F1"])
    )
    for i in xrange(n_tags):
        correct = count[i][i]
        predict = sum([count[j][i] for j in xrange(n_tags)])
        recall = count[i][i] * 100. / max(1, count[i].sum())
        precision = correct * 100. / max(predict, 1)
        f1 = 0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
        sResult += ("{}\t{}\t{}\t%s{}\t{}\t{}\t{}\t{}\t\n" % ("{}\t" * n_tags)).format(
            str(i), id_to_tag[i], str(count[i].sum()),
            *([count[i][j] for j in xrange(n_tags)] +
            [predict] +
            [correct] +
            ["%.3f" % (recall)] +
            ["%.3f" % (precision)] +
            ["%.3f" % (f1)]
              )
        )

    # Global accuracy

    temp_score = 100. * count.trace() / max(1, count.sum())
    sResult +=  "%i/%i (%.5f%%)" % (count.trace(), count.sum(), temp_score)

    # F1 on all entities
    return float(eval_lines[1].strip().split()[-1]), temp_score, sResult, eval_lines, log
    # return 0.0, temp_score, sResult, eval_lines, log


def predict(parameters, f_eval, raw_sentences, parsed_sentences,
            id_to_tag, output, add_O_tags = False):

    """
    predict tag
    """
    n_tags = len(id_to_tag)
    predictions = []
    count = np.zeros((n_tags, n_tags), dtype=np.int32)

    for raw_sentence, data in zip(raw_sentences, parsed_sentences):
        input = create_input2(data, parameters, False)
        if parameters['crf']:
            y_preds = np.array(f_eval(*input))[1:-1]
        else:
            y_preds = f_eval(*input).argmax(axis=1)
        y_reals = np.array(data['tags']).astype(np.int32)
        assert len(y_preds) == len(y_reals)
        p_tags = [id_to_tag[y_pred] for y_pred in y_preds]
        r_tags = [id_to_tag[y_real] for y_real in y_reals]
        if parameters['tag_scheme'] == 'iobes':
            p_tags = iobes_iob(p_tags)
            r_tags = iobes_iob(r_tags)
        for i, (y_pred, y_real) in enumerate(zip(y_preds, y_reals)):
            # new_line = "\t".join(raw_sentence[i][:-1] + [r_tags[i], p_tags[i]])
            if add_O_tags: # if True --> used for multilayer evaluatition
                new_line = "\t".join(raw_sentence[i][:-1] + [p_tags[i], "O"])
            else:
                new_line = "\t".join(raw_sentence[i][:-1] + [p_tags[i]])


            predictions.append(new_line)
            count[y_real, y_pred] += 1        

        predictions.append("")

    # Write predictions to disk and run CoNLL script externally
    if output != None:
        with codecs.open(output, 'w', 'utf8') as f:
            f.write("\n".join(predictions))
    else:
        return "\n".join(predictions)

def predict2(parameters, f_eval, raw_sentences, parsed_sentences,
            id_to_tag):

    """
    predict tag --> results is an arrays
    """
    predictions = []
    for raw_sentence, data in zip(raw_sentences, parsed_sentences):
        sentence = []
        input = create_input2(data, parameters, False)
        if parameters['crf']:
            y_preds = np.array(f_eval(*input))[1:-1]
        else:
            y_preds = f_eval(*input).argmax(axis=1)
        p_tags = [id_to_tag[y_pred] for y_pred in y_preds]
        if parameters['tag_scheme'] == 'iobes':
            p_tags = iobes_iob(p_tags)
        for i in range(len(y_preds)):
            sentence.append(raw_sentence[i][:-1] + [p_tags[i]])
        predictions.append(sentence)
    return predictions

def call_conlleval(output_path, scores_path):
    os.system("%s < %s > %s" % (eval_script, output_path, scores_path))
    eval_lines = [l.rstrip() for l in codecs.open(scores_path, 'r', 'utf8')]

    return eval_lines


# if __name__ == '__main__':
#     print iob_iobes(["B-S2", "I-S2", "O"])