from __future__ import absolute_import

import re
from decimal import Decimal

from dateutil import parser

from .errors import Error
from .log import logger

rep_sql_regx = re.compile("('\$\|.*?\|\$')")
limit_sql_regx = re.compile("(\sLIMIT\s*[0-9]{1,6})")
join_on_sql_regx = re.compile("JOIN.*\sON.*\s*WHERE", flags=re.DOTALL)


class Cursor(object):
    def __init__(self, connection):
        self.connection = connection
        self._arraysize = 1

        self.description = None
        self.rowcount = -1
        self.results = None
        self.fetched_rows = 0

    def callproc(self):
        raise ('Stored procedures not supported in Kylin')

    def close(self):
        logger.debug('Cursor close called')

    @staticmethod
    def trans_sql_for_kylin(sql_str):
        sql_str = sql_str
        rep_sql_list = rep_sql_regx.findall(sql_str)
        if not rep_sql_list:
            return sql_str
        transformed_sql = None
        rep_sql_dict = dict()
        rep_sql_set = set()
        for rep_sql in rep_sql_list:
            rep_sql_dict[rep_sql] = rep_sql.split('|')[-2]
            rep_sql_set.add(rep_sql.split('|')[1])

        # step 1, replace all the const str to data name
        for rep_sql, rep_value in rep_sql_dict.items():
            if transformed_sql:
                transformed_sql = transformed_sql.replace(rep_sql, rep_value)
            else:
                transformed_sql = sql_str.replace(rep_sql, rep_value)

        # step 2, replace limit number from sub-sql
        limit_sql_list = limit_sql_regx.findall(transformed_sql)
        if len(limit_sql_list) == 2:
            transformed_sql = transformed_sql.replace(limit_sql_list[1], limit_sql_list[0])

        # step 3, delete 'join on' sql
        join_on_sql = join_on_sql_regx.findall(transformed_sql)
        if join_on_sql:
            transformed_sql = transformed_sql.replace(join_on_sql[0], 'WHERE')

        # step 4, replace 'where' with rep_sql(inner join)
        for rep_sql in rep_sql_set:
            transformed_sql = transformed_sql.replace('WHERE', '%s %s' % (rep_sql, 'WHERE'))

        return transformed_sql

    def execute(self, operation, parameters={}, acceptPartial=True, limit=None, offset=0):
        if parameters:
            sql = operation % parameters
        else:
            sql = operation
        data = {
            'sql': self.trans_sql_for_kylin(sql),
            'offset': offset,
            'limit': limit or self.connection.limit,
            'acceptPartial': acceptPartial,
            'project': self.connection.project
        }
        logger.debug("RAW SQL=%s" % sql)
        logger.debug("Trans SQL=%s" % data.get('sql'))

        resp = self.connection.proxy.post('query', json=data)

        column_metas = resp['columnMetas']
        self.description = [
            [c['label'].lower(), c['columnTypeName'],
             c['displaySize'], 0,
             c['precision'], c['scale'], c['isNullable']]
            for c in column_metas
            ]

        self.results = [self._type_mapped(r) for r in resp['results']]
        self.rowcount = len(self.results)
        self.fetched_rows = 0
        return self.rowcount

    def _type_mapped(self, result):
        meta = self.description
        size = len(meta)
        for i in range(0, size):
            column = meta[i]
            tpe = column[1]
            val = result[i]
            if tpe == 'DATE':
                val = parser.parse(val)
            elif tpe == 'BIGINT' or tpe == 'INT' or tpe == 'TINYINT':
                val = int(val) if val else 0
            elif tpe == 'DOUBLE' or tpe == 'FLOAT':
                val = float(val) if val else 0.0
            elif tpe == 'BOOLEAN':
                val = (val == 'true')
            elif tpe.startswith('DECIMAL'):
                val = Decimal(val) if val else Decimal(0)
            result[i] = val
        return result

    def executemany(self, operation, seq_params=[]):
        results = []
        for param in seq_params:
            self.execute(operation, param)
            results.extend(self.results)
        self.results = results
        self.rowcount = len(self.results)
        self.fetched_rows = 0
        return self.rowcount

    def fetchone(self):
        if self.fetched_rows < self.rowcount:
            row = self.results[self.fetched_rows]
            self.fetched_rows += 1
            return row
        else:
            return None

    def fetchmany(self, size=None):
        fetched_rows = self.fetched_rows
        size = size or self.arraysize
        self.fetched_rows = fetched_rows + size
        return self.results[fetched_rows:self.fetched_rows]

    def fetchall(self):
        fetched_rows = self.fetched_rows
        self.fetched_rows = self.rowcount
        return self.results[fetched_rows:]

    def nextset(self):
        raise Error('Nextset operation not supported in Kylin')

    @property
    def arraysize(self):
        return self._arraysize

    @arraysize.setter
    def arraysize(self, array_size):
        self._arraysize = array_size

    def setinputsizes(self):
        logger.warn('setinputsize not supported in Kylin')

    def setoutputsize(self):
        logger.warn('setoutputsize not supported in Kylin')
