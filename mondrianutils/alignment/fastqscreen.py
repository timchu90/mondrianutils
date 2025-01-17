import csverve.api as csverve
import os
import pandas as pd
import shutil
from collections import defaultdict
from mondrianutils import helpers
from mondrianutils.alignment import fastq_utils
from mondrianutils.alignment.dtypes import dtypes
from subprocess import Popen, PIPE


def merge_fastq_screen_counts(
        all_detailed_counts, all_summary_counts, merged_detailed_counts, merged_summary_counts
):
    if isinstance(all_detailed_counts, dict):
        all_detailed_counts = all_detailed_counts.values()

    detailed_data = []
    for countsfile in all_detailed_counts:
        if os.stat(countsfile).st_size == 0:
            continue
        detailed_data.append(pd.read_csv(countsfile))

    df = pd.concat(detailed_data)

    index_cols = [v for v in df.columns.values if v != "count"]

    df['count'] = df.groupby(index_cols)['count'].transform('sum')

    df = df.drop_duplicates(subset=index_cols)

    csverve.write_dataframe_to_csv_and_yaml(
        df, merged_detailed_counts, dtypes()['fastqscreen_detailed'], write_header=True
    )

    if isinstance(all_summary_counts, dict):
        all_summary_counts = all_summary_counts.values()

    summary_counts = [pd.read_csv(countsfile) for countsfile in all_summary_counts]

    df = pd.concat(summary_counts)

    update_cols = [v for v in df.columns.values if v != 'cell_id']

    for colname in update_cols:
        df[colname] = df.groupby('cell_id')[colname].transform('sum')

    df = df.drop_duplicates(subset=['cell_id'])

    csverve.write_dataframe_to_csv_and_yaml(
        df, merged_summary_counts, dtypes()['metrics'], write_header=True
    )


def run_cmd(cmd, output=None):
    stdout = PIPE
    if output:
        stdout = open(output, "w")

    p = Popen(cmd, stdout=stdout, stderr=PIPE)

    cmdout, cmderr = p.communicate()
    retc = p.returncode

    if retc:
        raise Exception(
            "command failed. stderr:{}, stdout:{}".format(
                cmdout,
                cmderr))

    if output:
        stdout.close()


def run_fastq_screen_paired_end(fastq_r1, fastq_r2, tempdir, params):
    def get_basename(filepath):
        filepath_base = os.path.basename(filepath)

        if filepath_base.endswith('.fastq.gz'):
            filepath_base = filepath_base[:-len('.fastq.gz')]
        elif filepath_base.endswith('.fq.gz'):
            filepath_base = filepath_base[:-len('.fq.gz')]
        elif filepath_base.endswith('.fastq'):
            filepath_base = filepath_base[:-len('.fastq')]
        elif filepath_base.endswith('.fq'):
            filepath_base = filepath_base[:-len('.fq')]
        else:
            raise Exception('unknown file format. {}'.format(filepath))
        return filepath_base

    basename = get_basename(fastq_r1)
    tagged_fastq_r1 = os.path.join(tempdir, '{}.tagged.fastq.gz'.format(basename))

    basename = get_basename(fastq_r2)
    tagged_fastq_r2 = os.path.join(tempdir, '{}.tagged.fastq.gz'.format(basename))

    # fastq screen fails if run on empty files
    with helpers.getFileHandle(fastq_r1) as reader:
        if not reader.readline():
            shutil.copy(fastq_r1, tagged_fastq_r1)
            shutil.copy(fastq_r2, tagged_fastq_r2)
            return tagged_fastq_r1, tagged_fastq_r2

    config = os.path.join(tempdir, 'fastq_screen.config')

    with open(config, 'w') as config_writer:
        for genome in params['genomes']:
            genome_name = genome['name']
            genome_path = genome['path']
            outstr = '\t'.join(['DATABASE', genome_name, genome_path]) + '\n'
            config_writer.write(outstr)

    cmd = [
        'fastq_screen',
        '--aligner', params['aligner'],
        '--conf', config,
        '--outdir', tempdir,
        '--tag',
        fastq_r1,
        fastq_r2,
    ]

    run_cmd(cmd)

    return tagged_fastq_r1, tagged_fastq_r2


def write_detailed_counts(counts, outfile, cell_id, fastqscreen_params):
    header = None

    genomes = [genome['name'] for genome in fastqscreen_params['genomes']]

    with helpers.getFileHandle(outfile, 'wt') as writer:

        for read_end, read_end_counts in counts.items():

            if not read_end_counts and not header:
                outstr = ['cell_id', 'readend'] + genomes + ['count']
                writer.write(','.join(outstr) + '\n')
                header = 1
                continue

            if not header:
                outstr = ['cell_id', 'readend']
                outstr += [v[0] for v in list(read_end_counts.keys())[0]]
                outstr += ['count']
                writer.write(','.join(outstr) + '\n')
                header = 1

            for flags, count in read_end_counts.items():
                outstr = [cell_id, read_end]
                outstr += [v[1] for v in flags]
                outstr += [count]
                writer.write(','.join(map(str, outstr)) + '\n')


def write_summary_counts(counts, outfile, cell_id, fastqscreen_params):
    genomes = [genome['name'] for genome in fastqscreen_params['genomes']]

    summary_counts = {'nohit': 0, 'total_reads': 0}
    for genome in genomes:
        summary_counts[genome] = 0
        summary_counts['{}_multihit'.format(genome)] = 0

    for read_end, read_end_counts in counts.items():
        for flags, count in read_end_counts.items():
            summary_counts['total_reads'] += count
            hit_orgs = [v[0] for v in flags if v[1] > 0]

            for org in hit_orgs:
                summary_counts[org] += count

            if len(hit_orgs) > 1:
                for org in hit_orgs:
                    summary_counts['{}_multihit'.format(org)] += count
            elif len(hit_orgs) == 0:
                summary_counts['nohit'] += count

    with helpers.getFileHandle(outfile, 'wt') as writer:
        if not summary_counts:
            columns = ['cell_id']
            columns += ['fastqscreen_' + genome for genome in genomes]
            columns += ['fastqscreen_nohit', 'fastqscreen_total_reads']
            header = ','.join(columns) + '\n'
            writer.write(header)
            data = [0] * len(columns)
            data[0] = cell_id
            data = [str(v) for v in data]
            data = ','.join(data) + '\n'
            writer.write(data)
            return

        keys = sorted(summary_counts.keys())
        header = ['cell_id'] + ['fastqscreen_{}'.format(key) for key in keys]
        header = ','.join(header) + '\n'
        writer.write(header)

        values = [cell_id] + [summary_counts[v] for v in keys]
        values = ','.join(map(str, values)) + '\n'
        writer.write(values)


def re_tag_reads(infile, outfile):
    reader = fastq_utils.TaggedFastqReader(infile)

    with helpers.getFileHandle(outfile, 'wt') as writer:

        for read in reader.get_read_iterator():
            read = reader.add_tag_to_read_comment(read)

            for line in read:
                writer.write(line)


def organism_filter(
        fastq_r1, fastq_r2, filtered_fastq_r1, filtered_fastq_r2,
        detailed_metrics, summary_metrics, tempdir, cell_id,
        human_reference, mouse_reference, salmon_reference
):
    params = {
        'strict_validation': True,
        'filter_contaminated_reads': False,
        'aligner': 'bwa',
        'genomes': [
            {'name': 'grch37', 'path': human_reference},
            {'name': 'mm10', 'path': mouse_reference},
            {'name': 'salmon', 'path': salmon_reference}
        ]
    }

    # fastq screen tries to skip if files from old runs are available
    if os.path.exists(tempdir):
        shutil.rmtree(tempdir)

    helpers.makedirs(tempdir)

    tagged_fastq_r1, tagged_fastq_r2 = run_fastq_screen_paired_end(
        fastq_r1, fastq_r2, tempdir, params,
    )

    reader = fastq_utils.PairedTaggedFastqReader(tagged_fastq_r1, tagged_fastq_r2)
    counts = reader.gather_counts()

    write_detailed_counts(counts, detailed_metrics, cell_id, params)
    write_summary_counts(counts, summary_metrics, cell_id, params)

    # use the full tagged fastq downstream
    # with organism type information in readname
    re_tag_reads(tagged_fastq_r1, filtered_fastq_r1)
    re_tag_reads(tagged_fastq_r2, filtered_fastq_r2)
