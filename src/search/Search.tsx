import Kendra, { QueryRequest } from "aws-sdk/clients/kendra";
import { AWSError } from "aws-sdk/global";
import { PromiseResult } from "aws-sdk/lib/request";
import "bootstrap/dist/css/bootstrap.min.css";
import React, { ChangeEvent } from "react";
import { Spinner } from "react-bootstrap";
import { QueryResultType, Relevance } from "./constants";
import { getSampleIndexDetails } from "./exampleData/getSampleIndexDetails";
import { getSearchResults } from "./exampleData/getSearchResults";
import { AvailableFacetManager } from "./facets/AvailableFacetManager";
import { Facets } from "./facets/Facets";
import { SelectedFacetManager } from "./facets/SelectedFacetManager";
import {
  DataSourceNameLookup,
  getAttributeTypeLookup,
  getDataSourceNameLookup,
  IndexFieldNameToDocumentAttributeValueType,
} from "./facets/utils";
import Pagination from "./pagination/Pagination";
import ResultPanel from "./resultsPanel/ResultPanel";
import "./search.scss";
import SearchBar from "./searchBar/SearchBar";
import _ from "lodash";
import { AvailableSortingAttributesManager } from "./sorting/AvailableSortingAttributesManager";
import { SelectedSortingAttributeManager } from "./sorting/SelectedSortingAttributeManager";
import { DEFAULT_SORT_ATTRIBUTE, SortOrderEnum } from "./sorting/constants";
import S3 from "aws-sdk/clients/s3";
import { isNullOrUndefined } from "./utils";

interface SearchProps {
  /* An authenticated instance of the Kendra SDK */
  kendra?: Kendra;
  /* The ID of an index in the account the Kendra SDK is authenticated for */
  indexId: string;

  s3?: S3;
  accessToken?: string;
 
  facetConfiguration?: {
    facetsToShowWhenUncollapsed: number;
    showCount: boolean;
    updateAvailableFacetsWhenFilterChange: boolean;
    facetPanelDefaultOpen: boolean;
  };
}

interface SearchState {
  dataReady: boolean;
  searchResults: Kendra.QueryResult;
  topResults: Kendra.QueryResultItemList;
  faqResults: Kendra.QueryResultItemList;
  docResults: Kendra.QueryResultItemList;
  currentPageNumber: number;
  queryText: string;
  error?: AWSError;
  index?: Kendra.DescribeIndexResponse;
  facetsOpen: boolean;

  // Faceting state
  attributeTypeLookup?: IndexFieldNameToDocumentAttributeValueType;
  availableFacets: AvailableFacetManager;
  dataSourceNameLookup?: DataSourceNameLookup;
  selectedFacets: SelectedFacetManager;

  // Sorting state
  availableSortingAttributes: AvailableSortingAttributesManager;
  selectedSortingAttribute: SelectedSortingAttributeManager;
}

export default class Search extends React.Component<SearchProps, SearchState> {
  constructor(props: SearchProps) {
    super(props);

    this.state = {
      dataReady: false,
      searchResults: {},
      topResults: [],
      faqResults: [],
      docResults: [],
      currentPageNumber: 1,
      queryText: "",
      error: undefined,
      attributeTypeLookup: undefined,
      availableFacets: AvailableFacetManager.empty(),
      selectedFacets: SelectedFacetManager.empty(),
      index: undefined,
      facetsOpen:
        (this.props.facetConfiguration &&
          this.props.facetConfiguration.facetPanelDefaultOpen) ??
        false,
      availableSortingAttributes: AvailableSortingAttributesManager.empty(),
      selectedSortingAttribute: SelectedSortingAttributeManager.default(),
    };
  }

  async componentDidMount() {
    const { indexId, kendra } = this.props;

    if (kendra) {
      const listDataSourcePromise = this.listDataSources(kendra, indexId);

      try {
        // Create attribute type lookup from index
        const index = await kendra
          .describeIndex({
            Id: indexId,
          })
          .promise();

        this.setState({
          attributeTypeLookup: getAttributeTypeLookup(index),
          index: index,
        });

        // Get available sorting attributes from index meta data
        if (index.DocumentMetadataConfigurations) {
          this.setState({
            availableSortingAttributes: this.state.availableSortingAttributes.fromIndexMetadata(
              index.DocumentMetadataConfigurations
            ),
          });
        }

        // Create data source name lookup
        const dataSources = await listDataSourcePromise;
        this.setState({
          dataSourceNameLookup: getDataSourceNameLookup(dataSources),
        });
      } catch (e) {
        this.setState({
          error: e,
        });
      }
    } else {
      // The SDK is not configured, use mock data
      this.setState({
        ...getSampleIndexDetails(),
      });
    }
  }

  listDataSources = async (
    kendra: Kendra,
    indexId: string
  ): Promise<Kendra.DataSourceSummaryList | null> => {
    try {
      let listDsResponse: PromiseResult<
        Kendra.ListDataSourcesResponse,
        AWSError
      > | null = await kendra
        .listDataSources({
          IndexId: indexId,
        })
        .promise();

      const dataSources = listDsResponse.SummaryItems || [];

      while (listDsResponse?.$response.hasNextPage()) {
        const nextPage = listDsResponse.$response.nextPage();
        if (nextPage) {
          listDsResponse = await nextPage.promise();
          if (listDsResponse?.SummaryItems) {
            dataSources.push(...listDsResponse.SummaryItems);
          }
        } else {
          listDsResponse = null;
        }
      }

      return dataSources;
    } catch (e) {
      this.setState({
        error: e,
      });
    }

    return null;
  };

  private getResultsHelper = async (
    queryText: string,
    pageNumber: number,
    filter?: Kendra.AttributeFilter
  ) => {
    this.setState({ dataReady: false });

    let results: Kendra.QueryResult = getSearchResults(pageNumber, filter);

    const queryRequest: QueryRequest = {
      IndexId: this.props.indexId,
      QueryText: queryText,
      PageNumber: pageNumber,
      AttributeFilter: filter ? filter : undefined,
    };

    if (this.props.accessToken) {
      queryRequest.UserContext = {
        "Token": this.props.accessToken
      }
    }
    
    const sortingAttribute = this.state.selectedSortingAttribute.getSelectedSortingAttribute();
    const sortingOrder = this.state.selectedSortingAttribute.getSelectedSortingOrder();

    if (sortingAttribute !== DEFAULT_SORT_ATTRIBUTE) {
      queryRequest.SortingConfiguration = {
        DocumentAttributeKey: sortingAttribute,
        SortOrder: sortingOrder!,
      };
    }

    if (this.props.kendra) {
      try {
        results = await this.props.kendra.query(queryRequest).promise();
      } catch (e) {
        this.setState({
          searchResults: {},
          topResults: [],
          faqResults: [],
          docResults: [],
          dataReady: true,
          error: e,
        });
        return;
      }
    } else {
      console.error(
        "WARNING: No Kendra SDK instance provided, using dummy data"
      );
    }

    const tempTopResults: Kendra.QueryResultItemList = [];
    const tempFAQResults: Kendra.QueryResultItemList = [];
    const tempDocumentResults: Kendra.QueryResultItemList = [];

    if (results && results.ResultItems) {
      results.ResultItems.forEach((result: Kendra.QueryResultItem) => {
        let excerpt_page_no = undefined;
        result.DocumentAttributes!.forEach((attr: Kendra.DocumentAttribute) => {
          if (attr.Key === "_excerpt_page_number") {
            excerpt_page_no = attr.Value.LongValue;
          }
        });
        if (!isNullOrUndefined(this.props.s3) && result.DocumentURI) {
          let mediafile = false;
          let offset = "";
          if (result.DocumentURI.endsWith(".mp3") || result.DocumentURI.endsWith(".mp4")) {
            //Assume mediafile
            mediafile = true;
            const answerText = result.DocumentExcerpt!.Text;
            const mm = answerText!.indexOf("[");
            const nn = answerText!.indexOf("]", mm);
            offset = answerText!.substring(mm+1,nn);
          }
          try {
            let res = result.DocumentURI.split("/");
            if (res[2].startsWith("s3")) {
              //The URI points to an object in an S3 bucket
              //Get presigned url from s3
              let bucket = res[3];
              let key = res[4];
              for (var i = 5; i < res.length; i++) {
                key = key + "/" + res[i];
              }
              let params = { Bucket: bucket, Key: key };
              let url = this.props.s3!.getSignedUrl("getObject", params);
              result.DocumentURI = url;
            }
          } catch {
            // Just do nothing, so the documentURI are still as before
          }
          if (result.DocumentURI && excerpt_page_no) {
            result.DocumentURI = result.DocumentURI + "#page=" + excerpt_page_no;
          }
          if (mediafile){
            result.DocumentURI = result.DocumentURI + "#t=" + offset;
          }
        }
        switch (result.Type) {
          case QueryResultType.Answer:
            tempTopResults.push(result);
            break;
          case QueryResultType.QuestionAnswer:
            tempFAQResults.push(result);
            break;
          case QueryResultType.Document:
            tempDocumentResults.push(result);
            break;
          default:
            break;
        }
      });

      // Only update availableFacets in two situations:
      // 1. There is no filter
      // 2. There is filter and the updateAvailableFacetsWhenFilterChange flag is true
      if (
        !filter ||
        (filter &&
          this.props.facetConfiguration?.updateAvailableFacetsWhenFilterChange)
      ) {
        this.setState({
          availableFacets: AvailableFacetManager.fromQueryResult(results),
        });
      }

      this.setState({
        searchResults: results,
        topResults: tempTopResults,
        faqResults: tempFAQResults,
        docResults: tempDocumentResults,
        dataReady: true,
        error: undefined,
      });
    } else {
      this.setState({
        searchResults: {},
        topResults: tempTopResults,
        faqResults: tempFAQResults,
        docResults: tempDocumentResults,
        dataReady: true,
        error: undefined,
      });
    }
    this.setState({
      currentPageNumber: pageNumber,
      queryText: queryText,
    });
  };

  // When submitting query from search bar, reset facets and sorting attributes
  getResults = async (queryText: string, pageNumber: number = 1) => {
    this.setState(
      {
        selectedFacets: this.state.selectedFacets.clearAll(),
        selectedSortingAttribute: SelectedSortingAttributeManager.default(),
      },
      () => {
        this.getResultsHelper(queryText, pageNumber);
      }
    );
  };

  getResultsOnPageChanging = async (
    queryText: string,
    pageNumber: number = 1
  ) => {
    this.computeFilterAndReSubmitQuery(queryText, pageNumber);
  };

  submitFeedback = async (
    relevance: Relevance,
    resultItem: Kendra.QueryResultItem
  ) => {
    if (!this.props.kendra) {
      console.error(
        "WARNING: No Kendra SDK instance provided, submit feedback ignored"
      );

      return;
    } else if (!this.props.indexId) {
      console.error(
        "WARNING: No Kendra Index Id provided, submit feedback ignored"
      );

      return;
    }

    const queryResult = this.state.searchResults;
    if (relevance !== Relevance.Click) {
      // Explicit relevance feedback
      const feedbackRequest: Kendra.SubmitFeedbackRequest = {
        IndexId: this.props.indexId,
        QueryId: queryResult.QueryId as string,
        RelevanceFeedbackItems: [
          {
            RelevanceValue: relevance as string,
            ResultId: resultItem.Id as string,
          },
        ],
      };

      this.props.kendra.submitFeedback(feedbackRequest).promise();
    } else {
      // Click feedback
      const feedbackRequest: Kendra.Types.SubmitFeedbackRequest = {
        IndexId: this.props.indexId,
        QueryId: queryResult.QueryId as string,
        ClickFeedbackItems: [
          {
            ClickTime: new Date(),
            ResultId: resultItem.Id as string,
          },
        ],
      };

      this.props.kendra.submitFeedback(feedbackRequest).promise();
    }
  };

  private getErrorNotification = () => {
    return (
      <div className="error-div">
        {!_.isEmpty(this.state.error?.message)
          ? this.state.error?.message
          : this.state.error?.code}
      </div>
    );
  };

  private computeFilterAndReSubmitQuery(
    queryText: string,
    pageNumber: number = 1
  ) {
    const filter = this.state.selectedFacets.buildAttributeFilter(
      this.state.attributeTypeLookup
    );

    this.getResultsHelper(queryText, pageNumber, filter);
  }

  onSelectedFacetsChanged = (updatedSelectedFacets: SelectedFacetManager) => {
    this.setState(
      {
        selectedFacets: updatedSelectedFacets,
      },
      () => {
        this.computeFilterAndReSubmitQuery(this.state.queryText);
      }
    );
  };

  handleClickExpander = () => {
    this.setState({
      ...this.state,
      facetsOpen: !this.state.facetsOpen,
    });
  };

  onSortingAttributeChange = (event: ChangeEvent<HTMLSelectElement>) => {
    this.setState(
      {
        selectedSortingAttribute: this.state.selectedSortingAttribute.setSelectedSortingAttribute(
          event.currentTarget.value
        ),
      },
      () => {
        this.computeFilterAndReSubmitQuery(this.state.queryText);
      }
    );
  };

  onSortingOrderChange = (
    event: React.MouseEvent<HTMLButtonElement, MouseEvent>
  ) => {
    const sortingOrder = this.state.selectedSortingAttribute.getSelectedSortingOrder();
    if (sortingOrder === SortOrderEnum.Desc) {
      this.setState(
        {
          selectedSortingAttribute: this.state.selectedSortingAttribute.setSelectedSortingOrder(
            SortOrderEnum.Asc
          ),
        },
        () => {
          this.computeFilterAndReSubmitQuery(this.state.queryText);
        }
      );
    } else if (sortingOrder === SortOrderEnum.Asc) {
      this.setState(
        {
          selectedSortingAttribute: this.state.selectedSortingAttribute.setSelectedSortingOrder(
            SortOrderEnum.Desc
          ),
        },
        () => {
          this.computeFilterAndReSubmitQuery(this.state.queryText);
        }
      );
    }
  };

  render() {
    return (
      <div>
        {this.state.error && this.getErrorNotification()}
        <SearchBar onSubmit={this.getResults} />
        {this.state.queryText && this.state.dataReady && (
          <div className="search-container">
            <div className="search-facet-container">
              <Facets
                availableFacets={this.state.availableFacets}
                attributeTypeLookup={this.state.attributeTypeLookup}
                dataSourceNameLookup={this.state.dataSourceNameLookup}
                onSelectedFacetsChanged={this.onSelectedFacetsChanged}
                selectedFacets={this.state.selectedFacets}
                index={this.state.index}
                open={this.state.facetsOpen}
                onExpand={this.handleClickExpander}
              />
            </div>
            <div className="search-result-container">
              {this.state.searchResults.TotalNumberOfResults === 0 && (
                <div className="empty-results center-align">
                  Kendra didn't match any results to your query.
                </div>
              )}
              {this.state.searchResults.TotalNumberOfResults !== 0 && (
                <div>
                  <ResultPanel
                    results={this.state.searchResults}
                    topResults={this.state.topResults}
                    faqResults={this.state.faqResults}
                    docResults={this.state.docResults}
                    dataReady={this.state.dataReady}
                    currentPageNumber={this.state.currentPageNumber}
                    submitFeedback={this.submitFeedback}
                    availableSortingAttributes={
                      this.state.availableSortingAttributes
                    }
                    selectedSortingAttribute={
                      this.state.selectedSortingAttribute
                    }
                    onSortingAttributeChange={this.onSortingAttributeChange}
                    onSortingOrderChange={this.onSortingOrderChange}
                  />
                  <Pagination
                    queryText={this.state.queryText}
                    currentPageNumber={this.state.currentPageNumber}
                    onSubmit={this.getResultsOnPageChanging}
                    results={this.state.searchResults}
                  />
                </div>
              )}
            </div>
          </div>
        )}

        {this.state.queryText && !this.state.dataReady && (
          <div className="results-section center-align">
            <Spinner
              className="result-spinner"
              animation="border"
              variant="secondary"
            />
          </div>
        )}
      </div>
    );
  }
}
