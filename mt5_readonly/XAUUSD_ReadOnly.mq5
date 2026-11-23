// Read-only indicator. Quotes go to a local CSV. No account access or trading.
#property strict
#property indicator_chart_window
#property indicator_plots 0
input string QuoteSymbol = "XAUUSD";
input string OutputFile = "XAUUSD_readonly.csv";
ulong cursor = 0;
string cursorKey;

int OnInit() {
   if(QuoteSymbol != _Symbol) {
      Print("Attach to the configured gold symbol chart; no symbol substitution.");
      return INIT_PARAMETERS_INCORRECT;
   }
   cursorKey = "N1_readonly_" + QuoteSymbol;
   if(GlobalVariableCheck(cursorKey)) cursor=(ulong)GlobalVariableGet(cursorKey);
   else cursor=(ulong)TimeGMT()*1000-60000;
   EventSetTimer(1);
   Print("READ ONLY. Export: ", TerminalInfoString(TERMINAL_COMMONDATA_PATH), "\\Files\\", OutputFile);
   return INIT_SUCCEEDED;
}

void OnTimer() {
   if(!TerminalInfoInteger(TERMINAL_CONNECTED)) return;
   MqlTick ticks[];
   int count=CopyTicks(QuoteSymbol,ticks,COPY_TICKS_ALL,cursor,10000);
   if(count<=0) return;
   int file=FileOpen(OutputFile,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ,',');
   if(file==INVALID_HANDLE) { Print("Read-only export unavailable: ", GetLastError()); return; }
   if(FileSize(file)==0) FileWrite(file,"timestamp","symbol","bid","ask");
   FileSeek(file,0,SEEK_END);
   int digits=(int)SymbolInfoInteger(QuoteSymbol,SYMBOL_DIGITS);
   for(int i=0;i<count;i++) {
      if(ticks[i].bid<=0 || ticks[i].ask<ticks[i].bid) continue;
      FileWrite(file,(long)ticks[i].time_msc,QuoteSymbol,
                DoubleToString(ticks[i].bid,digits),DoubleToString(ticks[i].ask,digits));
   }
   FileFlush(file);
   FileClose(file);
   // Re-request the boundary millisecond, preserving order; Python deduplicates.
   // If the same millisecond saturates a batch, suspend rather than skip ticks.
   ulong latest=ticks[count-1].time_msc;
   if(count==10000 && latest==cursor) {
      Print("Ambiguous tick batch: suspended for audit");EventKillTimer();return;
   }
   cursor=latest;
   GlobalVariableSet(cursorKey,(double)cursor);
}

void OnDeinit(const int reason) { EventKillTimer(); }
int OnCalculate(const int rates_total,const int prev_calculated,const datetime &time[],
                const double &open[],const double &high[],const double &low[],const double &close[],
                const long &tick_volume[],const long &volume[],const int &spread[]) {
   return rates_total;
}
