#include <assert.h>
int main(void){ int x=3; if(x>0) x*=2; else x-=2; assert(x==6); return 0; }
