// Public market data only. No accounts, credentials or order endpoints.
import {getHistoricalRates} from 'dukascopy-node';
const [timeframe, from, to] = process.argv.slice(2);
if (!['tick', 'm1'].includes(timeframe) || !Number.isFinite(+from) || !Number.isFinite(+to) || +to <= +from) {
  throw new Error('Invalid data request');
}
try {
  const rows = await getHistoricalRates({instrument:'xauusd', timeframe,
    dates:{from:new Date(+from),to:new Date(+to)}, priceType:'bid', format:'json',
    useCache:false, wafMode:'off', batchSize:3, requestIntervalMs:150,
    retryCount:1, rateLimitRetryCount:1, failAfterRetryCount:true, ignoreFlats:false});
  process.stdout.write(JSON.stringify(rows));
} catch (error) {
  console.error('Public Dukascopy data unavailable: ' + error.name);
  process.exitCode=1;
}
